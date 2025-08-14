import os
import pandas as pd
import numpy as np
from scipy import stats
import logging
import random
import dash
from dash import dcc, html, Input, Output, State, dash_table, callback_context, ALL, MATCH
import dash_bootstrap_components as dbc
import plotly.graph_objs as go
import plotly.express as px
import plotly.figure_factory as ff
import yfinance as yf
from tqdm import tqdm
import pickle
import json
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
import threading
import multiprocessing
from threading import Lock
import re

# Import shared modules for parity with onepass
from signal_library.shared_symbols import normalize_ticker, detect_ticker_type
from signal_library.shared_integrity import (
    compute_stable_fingerprint,
    compute_quantized_fingerprint,
    check_head_tail_match,
    check_head_tail_match_fuzzy,
    evaluate_library_acceptance,
    verify_data_integrity,
    HEAD_TAIL_SNAPSHOT_SIZE,
    QUANTIZED_FINGERPRINT_PRECISION,
    HEAD_TAIL_ATOL_EQUITY,
    HEAD_TAIL_ATOL_CRYPTO,
    HEAD_TAIL_RTOL,
    HEAD_TAIL_MIN_MATCH_FRAC
)

# Note: CRYPTO_BASES and SAFE_BARE_CRYPTO_BASES now imported from shared_symbols module

# Global lock for yfinance (not thread-safe)
SMA_CACHE = {}  # Global SMA cache
yfinance_lock = Lock()
progress_lock = Lock()  # Thread-safe progress updates
import base64
import io
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    # Dummy classes to prevent errors if reportlab is not installed
    class colors:
        HexColor = lambda x: None
        whitesmoke = None
    class ParagraphStyle:
        pass
import warnings
import hashlib
warnings.filterwarnings('ignore')

# Import parity configuration
try:
    from signal_library.parity_config import (
        STRICT_PARITY_MODE, apply_strict_parity, get_tiebreak_signal,
        log_parity_status,
        EQUITY_SESSION_BUFFER_MINUTES, CRYPTO_STABILITY_MINUTES, LOG_ACCEPTANCE_TIER
    )
except ImportError:
    # Fallback if config not available
    STRICT_PARITY_MODE = False
    EQUITY_SESSION_BUFFER_MINUTES = 10
    CRYPTO_STABILITY_MINUTES = 60
    LOG_ACCEPTANCE_TIER = True
    def apply_strict_parity(df): return df
    def get_tiebreak_signal(buy_val, short_val):
        return 'Buy' if buy_val > short_val else 'Short' if short_val > buy_val else 'Short'
    def log_parity_status(): pass

# Remove all handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

# Ensure logs directory exists before creating FileHandler
os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler('logs/impactsearch.log', mode='w')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# Log reportlab status
if not REPORTLAB_AVAILABLE:
    logger.info("ReportLab not installed. PDF export will be disabled. Install with: pip install reportlab")

# Constants
MAX_SMA_DAY = 114
ENGINE_VERSION = "1.0.0"  # Version for Signal Library compatibility
SIGNAL_LIBRARY_DIR = "signal_library/data"  # Directory where onepass.py saves signals

# Precompute pairs once at module level for efficiency
PAIR_DTYPE = np.uint16 if MAX_SMA_DAY > 255 else np.uint8
PAIRS = np.array([(i, j) for i in range(1, MAX_SMA_DAY+1)
                  for j in range(1, MAX_SMA_DAY+1) if i != j], dtype=PAIR_DTYPE)
I_IDX = PAIRS[:, 0] - 1
J_IDX = PAIRS[:, 1] - 1  # Set fixed window for SMA calculations
CACHE_DIR = 'cache/impact_analysis'
CACHE_EXPIRY_DAYS = 7

# Global progress tracking
progress_tracker = {
    'current_ticker': '',
    'current_index': 0,
    'total_tickers': 0,
    'start_time': None,
    'results': [],
    'status': 'idle'
}

def safe_divide(numerator, denominator, default=0):
    """Safe division with default value"""
    if denominator == 0 or not np.isfinite(denominator):
        return default
    result = numerator / denominator
    if not np.isfinite(result):
        return default
    return result


class VisualMetrics:
    """Class for creating visual metric components similar to spymaster.py"""
    
    @staticmethod
    def create_performance_card(title, value, subtitle="", icon="📊", color="#00ff41", glow=False):
        """Create a performance metric card with consistent styling"""
        
        # Determine glow intensity based on value
        glow_effect = ""
        if glow:
            if isinstance(value, (int, float)):
                if value > 2:
                    glow_effect = "0 0 20px rgba(0, 255, 65, 0.5)"
                elif value > 1:
                    glow_effect = "0 0 15px rgba(0, 255, 65, 0.3)"
                elif value > 0:
                    glow_effect = "0 0 10px rgba(255, 255, 0, 0.3)"
                else:
                    glow_effect = "0 0 10px rgba(255, 0, 64, 0.3)"
        
        card_style = {
            'backgroundColor': 'rgba(0, 0, 0, 0.6)',
            'border': f'1px solid {color}',
            'borderRadius': '10px',
            'padding': '20px',
            'height': '100%',
            'boxShadow': glow_effect
        }
        
        return dbc.Card([
            dbc.CardBody([
                html.Div([
                    html.Span(icon, style={'fontSize': '2rem', 'marginRight': '10px'}),
                    html.Span(title, style={'fontSize': '0.9rem', 'color': '#888'})
                ], style={'marginBottom': '10px'}),
                html.H3(str(value), style={'color': color, 'marginBottom': '5px'}),
                html.P(subtitle, style={'fontSize': '0.8rem', 'color': '#aaa', 'marginBottom': '0'})
            ])
        ], style=card_style)
    
    @staticmethod
    def create_sharpe_gauge(sharpe_ratio):
        """Create a gauge chart for Sharpe ratio visualization"""
        
        # Determine color based on Sharpe ratio
        if sharpe_ratio >= 2:
            color = "#00ff41"
            rating = "Excellent"
        elif sharpe_ratio >= 1:
            color = "#80ff00"
            rating = "Good"
        elif sharpe_ratio >= 0:
            color = "#ffff00"
            rating = "Fair"
        else:
            color = "#ff0040"
            rating = "Poor"
        
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=sharpe_ratio,
            title={'text': f"Sharpe Ratio - {rating}"},
            domain={'x': [0, 1], 'y': [0, 1]},
            gauge={
                'axis': {'range': [None, 3], 'tickwidth': 1, 'tickcolor': "darkgray"},
                'bar': {'color': color},
                'bgcolor': "rgba(0,0,0,0.1)",
                'borderwidth': 2,
                'bordercolor': "gray",
                'steps': [
                    {'range': [0, 1], 'color': 'rgba(255, 255, 0, 0.1)'},
                    {'range': [1, 2], 'color': 'rgba(128, 255, 0, 0.1)'},
                    {'range': [2, 3], 'color': 'rgba(0, 255, 65, 0.1)'}
                ],
                'threshold': {
                    'line': {'color': "white", 'width': 4},
                    'thickness': 0.75,
                    'value': 1
                }
            }
        ))
        
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font={'color': '#00ff41'},
            height=250
        )
        
        return fig
    
    @staticmethod
    def create_significance_meter(p_value):
        """Create a significance level meter"""
        
        if p_value == 'N/A':
            significance_level = 0
            status = "No Data"
            color = "#808080"
        else:
            p_val = float(p_value) if isinstance(p_value, str) else p_value
            if p_val < 0.01:
                significance_level = 99
                status = "99% Significant"
                color = "#00ff41"
            elif p_val < 0.05:
                significance_level = 95
                status = "95% Significant"
                color = "#80ff00"
            elif p_val < 0.10:
                significance_level = 90
                status = "90% Significant"
                color = "#ffff00"
            else:
                significance_level = 0
                status = "Not Significant"
                color = "#ff0040"
        
        return html.Div([
            html.Label(f"Statistical Significance: {status}", 
                      style={'fontSize': '0.9rem', 'color': color, 'marginBottom': '10px'}),
            dbc.Progress(
                value=significance_level,
                color="success" if significance_level >= 95 else "warning" if significance_level >= 90 else "danger",
                style={'height': '25px'},
                className="mb-3",
                animated=significance_level > 0,
                striped=significance_level > 0
            ),
            html.P(f"p-value: {p_value}", style={'fontSize': '0.8rem', 'color': '#aaa'})
        ])
    
    @staticmethod
    def create_win_rate_visual(wins, losses):
        """Create a visual representation of win rate"""
        total = wins + losses
        if total == 0:
            win_rate = 0
        else:
            win_rate = (wins / total) * 100
        
        # Determine emoji and color
        if win_rate >= 60:
            emoji = "🎯"
            color = "#00ff41"
            status = "Strong"
        elif win_rate >= 50:
            emoji = "✅"
            color = "#80ff00"
            status = "Positive"
        elif win_rate >= 40:
            emoji = "⚠️"
            color = "#ffff00"
            status = "Weak"
        else:
            emoji = "❌"
            color = "#ff0040"
            status = "Poor"
        
        return html.Div([
            html.Div([
                html.Span(f"{emoji} ", style={'fontSize': '1.5rem'}),
                html.Span(f"Win Rate: {win_rate:.1f}% ({status})", 
                         style={'fontSize': '1rem', 'color': color, 'fontWeight': 'bold'})
            ], style={'marginBottom': '10px'}),
            html.Div([
                html.Div([
                    html.Span("Wins", style={'color': '#00ff41', 'marginRight': '10px'}),
                    html.Span(str(wins), style={'fontWeight': 'bold', 'color': '#00ff41'})
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Span("Losses", style={'color': '#ff0040', 'marginRight': '10px'}),
                    html.Span(str(losses), style={'fontWeight': 'bold', 'color': '#ff0040'})
                ], style={'display': 'inline-block'})
            ])
        ])
    
    @staticmethod
    def create_correlation_heatmap(results_df):
        """Create a correlation heatmap for key metrics"""
        if len(results_df) < 3:
            return html.Div("Need at least 3 tickers for correlation analysis", 
                          style={'color': '#aaa', 'textAlign': 'center', 'padding': '20px'})
        
        # Select numeric columns for correlation
        numeric_cols = ['Trigger Days', 'Wins', 'Losses', 'Win Ratio (%)', 
                       'Std Dev (%)', 'Sharpe Ratio', 'Avg Daily Capture (%)', 
                       'Total Capture (%)']
        
        # Filter to existing columns
        available_cols = [col for col in numeric_cols if col in results_df.columns]
        
        if len(available_cols) < 2:
            return html.Div("Insufficient numeric data for correlation", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        # Calculate correlation matrix
        corr_matrix = results_df[available_cols].corr()
        
        # Create heatmap
        fig = ff.create_annotated_heatmap(
            z=corr_matrix.values,
            x=list(corr_matrix.columns),
            y=list(corr_matrix.index),
            colorscale='Viridis',
            showscale=True,
            reversescale=False
        )
        
        fig.update_layout(
            title="Metrics Correlation Heatmap",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=500,
            xaxis={'side': 'bottom'},
            yaxis={'autorange': 'reversed'}
        )
        
        # Update annotation text color
        for i in range(len(fig.layout.annotations)):
            fig.layout.annotations[i].font.color = '#fff'
        
        return dcc.Graph(figure=fig)
    
    @staticmethod
    def create_advanced_scatter_matrix(results_df):
        """Create an advanced scatter matrix plot"""
        if len(results_df) < 3:
            return html.Div("Need at least 3 tickers for scatter matrix", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        # Select key metrics
        metrics = ['Sharpe Ratio', 'Win Ratio (%)', 'Total Capture (%)']
        available_metrics = [m for m in metrics if m in results_df.columns]
        
        if len(available_metrics) < 2:
            return html.Div("Insufficient metrics for scatter matrix", 
                          style={'color': '#aaa', 'textAlign': 'center'})
        
        fig = px.scatter_matrix(
            results_df,
            dimensions=available_metrics,
            color='Sharpe Ratio',
            color_continuous_scale='Viridis',
            title="Metrics Scatter Matrix",
            labels={col: col.replace(' (%)', '') for col in available_metrics},
            hover_data=['Primary Ticker']
        )
        
        fig.update_traces(diagonal_visible=False, showupperhalf=False)
        
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=600,
            showlegend=False
        )
        
        return dcc.Graph(figure=fig)

class SummaryAnalyzer:
    """Generate intelligent summary and recommendations from analysis results"""
    
    @staticmethod
    def analyze_key_findings(results_df):
        """Extract key findings from results"""
        findings = []
        
        if len(results_df) == 0:
            return findings
        
        # Top performers
        top_sharpe = results_df.nlargest(3, 'Sharpe Ratio')
        if len(top_sharpe) > 0:
            findings.append({
                'type': 'top_performers',
                'title': '🏆 Top Performers by Sharpe Ratio',
                'description': f"Best performers: {', '.join(top_sharpe['Primary Ticker'].tolist())}",
                'details': f"Sharpe ratios ranging from {top_sharpe['Sharpe Ratio'].min():.2f} to {top_sharpe['Sharpe Ratio'].max():.2f}"
            })
        
        # Statistical significance findings
        sig_95 = results_df[results_df['Significant 95%'] == 'Yes']
        if len(sig_95) > 0:
            findings.append({
                'type': 'statistical_significance',
                'title': '📊 Statistically Significant Relationships',
                'description': f"{len(sig_95)} tickers show 95% statistical significance",
                'details': f"Tickers: {', '.join(sig_95['Primary Ticker'].head(5).tolist())}" + 
                          (" and more..." if len(sig_95) > 5 else "")
            })
        
        # Win rate analysis
        high_win_rate = results_df[results_df['Win Ratio (%)'] > 60]
        if len(high_win_rate) > 0:
            findings.append({
                'type': 'win_rate',
                'title': '🎯 High Win Rate Tickers',
                'description': f"{len(high_win_rate)} tickers with >60% win rate",
                'details': f"Best win rate: {results_df['Win Ratio (%)'].max():.1f}% ({results_df.loc[results_df['Win Ratio (%)'].idxmax(), 'Primary Ticker']})"
            })
        
        # Volatility patterns
        low_vol = results_df[results_df['Std Dev (%)'] < results_df['Std Dev (%)'].quantile(0.25)]
        if len(low_vol) > 0:
            findings.append({
                'type': 'volatility',
                'title': '🛡️ Low Volatility Performers',
                'description': f"{len(low_vol)} tickers with below-average volatility",
                'details': f"Most stable: {low_vol.nsmallest(1, 'Std Dev (%)')['Primary Ticker'].iloc[0]} ({low_vol['Std Dev (%)'].min():.2f}% std dev)"
            })
        
        return findings
    
    @staticmethod
    def detect_patterns(results_df):
        """Detect interesting patterns and correlations"""
        patterns = []
        
        if len(results_df) < 3:
            return patterns
        
        # Sector clustering (if we can infer from ticker names)
        tech_tickers = ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMD', 'INTC', 'CSCO', 'ORCL', 'CRM']
        tech_in_results = results_df[results_df['Primary Ticker'].isin(tech_tickers)]
        
        if len(tech_in_results) >= 3:
            avg_sharpe_tech = tech_in_results['Sharpe Ratio'].mean()
            avg_sharpe_all = results_df['Sharpe Ratio'].mean()
            if avg_sharpe_tech > avg_sharpe_all * 1.2:
                patterns.append({
                    'type': 'sector_trend',
                    'title': '💻 Technology Sector Outperformance',
                    'description': f"Tech stocks showing {((avg_sharpe_tech/avg_sharpe_all - 1) * 100):.1f}% better Sharpe ratio",
                    'recommendation': 'Consider analyzing more technology sector stocks'
                })
        
        # Market cap patterns
        mega_caps = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'LLY', 'TSM', 'V']
        mega_in_results = results_df[results_df['Primary Ticker'].isin(mega_caps)]
        
        if len(mega_in_results) >= 2:
            if mega_in_results['Win Ratio (%)'].mean() > 55:
                patterns.append({
                    'type': 'market_cap_trend',
                    'title': '🏢 Mega-Cap Stability',
                    'description': f"Large-cap stocks showing consistent win rates (avg: {mega_in_results['Win Ratio (%)'].mean():.1f}%)",
                    'recommendation': 'Mega-caps may provide more stable signals'
                })
        
        # Correlation clusters
        if 'Total Capture (%)' in results_df.columns and 'Sharpe Ratio' in results_df.columns:
            correlation = results_df['Total Capture (%)'].corr(results_df['Sharpe Ratio'])
            if abs(correlation) > 0.7:
                patterns.append({
                    'type': 'correlation',
                    'title': '🔗 Strong Metric Correlation',
                    'description': f"Total Capture and Sharpe Ratio correlation: {correlation:.2f}",
                    'recommendation': 'Focus on maximizing total capture for better risk-adjusted returns'
                })
        
        return patterns
    
    @staticmethod
    def generate_recommendations(results_df, secondary_ticker):
        """Generate actionable recommendations for follow-up analysis"""
        recommendations = []
        
        if len(results_df) == 0:
            return recommendations
        
        # Recommendation 1: Deep dive on top performers
        top_performers = results_df.nlargest(3, 'Sharpe Ratio')['Primary Ticker'].tolist()
        if top_performers:
            recommendations.append({
                'id': 'deep_dive_top',
                'title': '🔍 Deep Dive Analysis',
                'description': f"Perform detailed backtesting on top performers: {', '.join(top_performers)}",
                'action': 'deep_dive',
                'params': {
                    'tickers': top_performers,
                    'secondary': secondary_ticker,
                    'analysis_type': 'detailed_backtest'
                }
            })
        
        # Recommendation 2: Explore similar tickers
        if len(results_df) > 0:
            best_ticker = results_df.loc[results_df['Sharpe Ratio'].idxmax(), 'Primary Ticker']
            recommendations.append({
                'id': 'explore_similar',
                'title': '🔄 Find Similar Tickers',
                'description': f"Find tickers with similar characteristics to {best_ticker}",
                'action': 'find_similar',
                'params': {
                    'reference_ticker': best_ticker,
                    'secondary': secondary_ticker,
                    'metric': 'correlation'
                }
            })
        
        # Recommendation 3: Outlier investigation
        outliers = results_df[
            (results_df['Sharpe Ratio'] > results_df['Sharpe Ratio'].quantile(0.95)) |
            (results_df['Sharpe Ratio'] < results_df['Sharpe Ratio'].quantile(0.05))
        ]
        if len(outliers) > 0:
            recommendations.append({
                'id': 'investigate_outliers',
                'title': '⚠️ Investigate Outliers',
                'description': f"Analyze {len(outliers)} outlier tickers for special patterns",
                'action': 'outlier_analysis',
                'params': {
                    'outlier_tickers': outliers['Primary Ticker'].tolist(),
                    'secondary': secondary_ticker
                }
            })
        
        # Recommendation 4: Time period analysis
        if len(results_df) >= 5:
            recommendations.append({
                'id': 'time_period_analysis',
                'title': '📅 Time Period Optimization',
                'description': "Test different time periods to find optimal holding periods",
                'action': 'time_analysis',
                'params': {
                    'top_tickers': results_df.nlargest(5, 'Sharpe Ratio')['Primary Ticker'].tolist(),
                    'secondary': secondary_ticker,
                    'periods': [30, 60, 90, 180, 365]
                }
            })
        
        # Recommendation 5: Sector rotation analysis
        recommendations.append({
            'id': 'sector_rotation',
            'title': '🔄 Sector Rotation Analysis',
            'description': "Analyze sector rotation patterns for better timing",
            'action': 'sector_analysis',
            'params': {
                'secondary': secondary_ticker,
                'sectors': ['XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLY', 'XLP', 'XLRE', 'XLB', 'XLU']
            }
        })
        
        return recommendations
    
    @staticmethod
    def create_summary_visualizations(results_df):
        """Create summary visualizations"""
        visualizations = []
        
        if len(results_df) < 3:
            return visualizations
        
        # Performance distribution chart
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Histogram(
            x=results_df['Sharpe Ratio'],
            nbinsx=20,
            name='Sharpe Ratio Distribution',
            marker_color='#00ff41',
            opacity=0.7
        ))
        fig_dist.update_layout(
            title="Sharpe Ratio Distribution",
            xaxis_title="Sharpe Ratio",
            yaxis_title="Count",
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=300
        )
        visualizations.append(('distribution', fig_dist))
        
        # Risk-Return scatter
        fig_scatter = go.Figure()
        fig_scatter.add_trace(go.Scatter(
            x=results_df['Std Dev (%)'],
            y=results_df['Total Capture (%)'],
            mode='markers+text',
            text=results_df['Primary Ticker'],
            textposition='top center',
            marker=dict(
                size=results_df['Win Ratio (%)'] / 5,  # Size based on win ratio
                color=results_df['Sharpe Ratio'],
                colorscale='Viridis',
                showscale=True,
                colorbar=dict(title="Sharpe<br>Ratio"),
                line=dict(width=1, color='#00ff41')
            ),
            hovertemplate='<b>%{text}</b><br>' +
                         'Risk (Std): %{x:.2f}%<br>' +
                         'Return: %{y:.2f}%<br>' +
                         '<extra></extra>'
        ))
        fig_scatter.update_layout(
            title="Risk-Return Profile",
            xaxis_title="Risk (Std Dev %)",
            yaxis_title="Return (Total Capture %)",
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            height=400
        )
        visualizations.append(('risk_return', fig_scatter))
        
        return visualizations

class AnalysisTemplates:
    """Manage analysis templates and configurations"""
    
    TEMPLATES_DIR = 'cache/templates'
    
    @staticmethod
    def save_template(name, config):
        """Save an analysis template"""
        os.makedirs(AnalysisTemplates.TEMPLATES_DIR, exist_ok=True)
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        try:
            with open(template_path, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info(f"Saved template: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save template {name}: {e}")
            return False
    
    @staticmethod
    def load_template(name):
        """Load an analysis template"""
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        if not os.path.exists(template_path):
            return None
        
        try:
            with open(template_path, 'r') as f:
                config = json.load(f)
            logger.info(f"Loaded template: {name}")
            return config
        except Exception as e:
            logger.error(f"Failed to load template {name}: {e}")
            return None
    
    @staticmethod
    def list_templates():
        """List all available templates"""
        if not os.path.exists(AnalysisTemplates.TEMPLATES_DIR):
            return []
        
        templates = []
        for file in os.listdir(AnalysisTemplates.TEMPLATES_DIR):
            if file.endswith('.json'):
                templates.append(file[:-5])  # Remove .json extension
        
        return sorted(templates)
    
    @staticmethod
    def delete_template(name):
        """Delete a template"""
        template_path = os.path.join(AnalysisTemplates.TEMPLATES_DIR, f"{name}.json")
        
        if os.path.exists(template_path):
            try:
                os.remove(template_path)
                logger.info(f"Deleted template: {name}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete template {name}: {e}")
                return False
        return False

class ReportGenerator:
    """Generate PDF reports from analysis results"""
    
    @staticmethod
    def generate_pdf_report(results_data, secondary_ticker, filename=None):
        """Generate a comprehensive PDF report"""
        if not REPORTLAB_AVAILABLE:
            logger.warning("PDF generation skipped - ReportLab not installed")
            return None
        
        # Convert to DataFrame if needed
        if isinstance(results_data, list):
            results_df = pd.DataFrame(results_data)
        else:
            results_df = results_data
            
        if filename is None:
            # Ensure output directory exists
            output_dir = 'output/impactsearch'
            os.makedirs(output_dir, exist_ok=True)
            filename = f"{output_dir}/{secondary_ticker}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Create the PDF document
        doc = SimpleDocTemplate(filename, pagesize=landscape(letter))
        story = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#00ff41'),
            spaceAfter=30,
            alignment=1  # Center
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#00ff41'),
            spaceAfter=12
        )
        
        # Title
        story.append(Paragraph(f"Impact Analysis Report - {secondary_ticker}", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Summary statistics
        story.append(Paragraph("Executive Summary", heading_style))
        
        summary_data = [
            ['Metric', 'Value'],
            ['Total Tickers Analyzed', str(len(results_df))],
            ['Average Sharpe Ratio', f"{results_df['Sharpe Ratio'].mean():.2f}"],
            ['Best Performer', results_df.loc[results_df['Sharpe Ratio'].idxmax(), 'Primary Ticker']],
            ['Worst Performer', results_df.loc[results_df['Sharpe Ratio'].idxmin(), 'Primary Ticker']],
            ['95% Significant Count', str(len(results_df[results_df['Significant 95%'] == 'Yes']))],
            ['Average Win Ratio', f"{results_df['Win Ratio (%)'].mean():.1f}%"]
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003300')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#1a1a1a')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#00ff41')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#00ff41'))
        ]))
        
        story.append(summary_table)
        story.append(PageBreak())
        
        # Detailed results table
        story.append(Paragraph("Detailed Results", heading_style))
        
        # Prepare data for table
        table_columns = ['Primary Ticker', 'Sharpe Ratio', 'Win Ratio (%)', 
                        'Total Capture (%)', 'p-Value', 'Significant 95%']
        table_data = [table_columns]
        
        for _, row in results_df.iterrows():
            row_data = [str(row[col]) if col in row else 'N/A' for col in table_columns]
            table_data.append(row_data)
        
        results_table = Table(table_data, repeatRows=1)
        results_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003300')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#1a1a1a')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#80ff00')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#00ff41')),
            ('FONTSIZE', (0, 1), (-1, -1), 8)
        ]))
        
        story.append(results_table)
        
        # Build the PDF
        doc.build(story)
        logger.info(f"PDF report generated: {filename}")
        return filename

class CacheManager:
    """Manage caching for ticker data and calculations"""
    
    @staticmethod
    def get_cache_path(ticker, data_type='data'):
        """Generate cache file path"""
        os.makedirs(CACHE_DIR, exist_ok=True)
        return os.path.join(CACHE_DIR, f"{ticker}_{data_type}.pkl")
    
    @staticmethod
    def is_cache_valid(cache_path):
        """Check if cache file exists and is recent"""
        if not os.path.exists(cache_path):
            return False
        
        file_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
        if datetime.now() - file_time > timedelta(days=CACHE_EXPIRY_DAYS):
            return False
        
        return True
    
    @staticmethod
    def save_to_cache(data, ticker, data_type='data'):
        """Save data to cache"""
        cache_path = CacheManager.get_cache_path(ticker, data_type)
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"Cached {data_type} for {ticker}")
        except Exception as e:
            logger.error(f"Failed to cache {data_type} for {ticker}: {e}")
    
    @staticmethod
    def load_from_cache(ticker, data_type='data'):
        """Load data from cache"""
        cache_path = CacheManager.get_cache_path(ticker, data_type)
        
        if not CacheManager.is_cache_valid(cache_path):
            return None
        
        try:
            with open(cache_path, 'rb') as f:
                data = pickle.load(f)
            logger.debug(f"Loaded {data_type} from cache for {ticker}")
            return data
        except Exception as e:
            logger.error(f"Failed to load cache for {ticker}: {e}")
            return None

def deduplicate_tickers(tickers):
    """Remove duplicates after normalization"""
    if not tickers:
        return []
    
    normalized = []
    seen = set()
    
    for ticker in tickers:
        norm_ticker = normalize_ticker(ticker)
        if norm_ticker and norm_ticker not in seen:
            seen.add(norm_ticker)
            normalized.append(norm_ticker)
    
    logger.info(f"Deduplicated {len(tickers)} tickers to {len(normalized)} unique tickers")
    return normalized

# Note: fingerprint and integrity functions now imported from shared_integrity module

def load_signal_library(ticker):
    """
    Load Signal Library for a ticker from onepass.py's saved signals.
    Returns the signal data if found, None otherwise.
    """
    try:
        stable_dir = os.path.join(SIGNAL_LIBRARY_DIR, "stable")
        filename = f"{ticker}_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl"
        filepath = os.path.join(stable_dir, filename)
        
        if os.path.exists(filepath):
            try:
                with open(filepath, 'rb') as f:
                    signal_data = pickle.load(f)
            except (pickle.UnpicklingError, EOFError) as e:
                logger.error(f"Corrupt Signal Library for {ticker}: {e}")
                # Quarantine corrupt file for debugging
                corrupt_filepath = filepath + '.corrupt'
                os.replace(filepath, corrupt_filepath)
                logger.info(f"Renamed corrupt file to {corrupt_filepath}")
                return None
            
            # Verify version compatibility with detailed logging
            stored_version = signal_data.get('engine_version')
            stored_max_sma = signal_data.get('max_sma_day')
            
            if stored_version != ENGINE_VERSION:
                logger.warning(f"Version mismatch for {ticker}: stored={stored_version}, current={ENGINE_VERSION}")
            if stored_max_sma != MAX_SMA_DAY:
                logger.warning(f"MAX_SMA_DAY mismatch for {ticker}: stored={stored_max_sma}, current={MAX_SMA_DAY}")
            
            if stored_version == ENGINE_VERSION and stored_max_sma == MAX_SMA_DAY:
                logger.info(f"Signal Library loaded for {ticker} from {filepath}")
                
                # Check if this is the enhanced V2 format with primary_signals
                if 'primary_signals' in signal_data:
                    logger.info(f"  Enhanced V2 format detected with {len(signal_data['primary_signals'])} signals")
                
                return signal_data
            else:
                logger.warning(f"Version mismatch for {ticker} Signal Library")
                return None
        else:
            logger.debug(f"No Signal Library found for {ticker} at {filepath}")
            return None
            
    except Exception as e:
        logger.error(f"Error loading Signal Library for {ticker}: {e}")
        return None

# Note: normalize_ticker is now imported from shared_symbols module

def is_session_complete(df, ticker_type='equity'):
    """
    Mirror onepass.py behavior (equity cutoff 16:10 ET, crypto stability window)
    """
    from datetime import datetime, time, timedelta
    import pytz
    
    if df.empty:
        return True
    
    tz = pytz.timezone('America/New_York')
    now = datetime.now(tz)
    last_date = df.index[-1]
    
    if ticker_type == 'equity':
        # Use same formulation as onepass for consistency
        cutoff_dt = tz.localize(datetime.combine(now.date(), time(16, 0))) + timedelta(minutes=EQUITY_SESSION_BUFFER_MINUTES)
        if last_date.date() == now.date() and now < cutoff_dt:
            return False
    elif ticker_type == 'crypto':
        # For crypto daily bars: check if stamped with today's UTC date (match onepass.py)
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        # Treat naive index as UTC midnight for daily bars
        last_ts_utc = pd.Timestamp(last_date).tz_localize('UTC') if last_date.tzinfo is None else last_date.tz_convert('UTC')
        
        # Daily bar still forming if stamped with today's UTC date
        if last_ts_utc.date() == now_utc.date():
            logger.debug("Crypto daily bar for today is incomplete. Dropping it.")
            return False
        
        # Optional extra guard (mostly relevant if you add intraday later)
        minutes_old = (now_utc - last_ts_utc).total_seconds() / 60
        if minutes_old < CRYPTO_STABILITY_MINUTES:
            logger.debug(f"Crypto bar only {minutes_old:.1f} min old (<{CRYPTO_STABILITY_MINUTES}). Dropping it.")
            return False
    
    return True

def _coerce_to_close_frame(df):
    """
    Helper function to handle various column structures from yfinance.
    Ensures we always get a clean DataFrame with a single 'Close' column.
    """
    if df.empty:
        return pd.DataFrame()
    
    # Handle MultiIndex columns (occurs with some tickers like CTM)
    if isinstance(df.columns, pd.MultiIndex):
        # Try to extract the Close column from the MultiIndex
        if 'Adj Close' in df.columns.get_level_values(0):
            # Get the ticker symbol from the second level
            ticker_col = df.columns.get_level_values(1)[0] if len(df.columns.get_level_values(1)) > 0 else None
            if ticker_col and ('Adj Close', ticker_col) in df.columns:
                result = pd.DataFrame(df[('Adj Close', ticker_col)])
                result.columns = ['Close']
                return result
        if 'Close' in df.columns.get_level_values(0):
            ticker_col = df.columns.get_level_values(1)[0] if len(df.columns.get_level_values(1)) > 0 else None
            if ticker_col and ('Close', ticker_col) in df.columns:
                result = pd.DataFrame(df[('Close', ticker_col)])
                result.columns = ['Close']
                return result
    
    # Handle regular columns
    if 'Adj Close' in df.columns:
        return pd.DataFrame(df[['Adj Close']].rename(columns={'Adj Close': 'Close'}))
    elif 'Close' in df.columns:
        return pd.DataFrame(df[['Close']])
    
    # If we can't find a close column, return empty
    logger.error(f"No Close/Adj Close data found in DataFrame")
    return pd.DataFrame()

def fetch_data(ticker, use_cache=True, max_retries=3):
    """Fetch data with optional caching support"""
    if not ticker or not ticker.strip():
        return pd.DataFrame()

    original = ticker
    ticker = normalize_ticker(ticker)
    if original and original.strip().upper() != ticker:
        logger.info(f"Normalized ticker: {original.strip()} -> {ticker}")
    
    # Try to load from cache first (only if caching is enabled)
    if use_cache:
        cached_data = CacheManager.load_from_cache(ticker, 'data')
        if cached_data is not None:
            logger.info(f"Using cached data for {ticker}")
            return cached_data
    
    # Enhanced retry logic with exponential backoff
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching fresh data for {ticker} (attempt {attempt+1}/{max_retries})...")
            # Use lock for yfinance download (not thread-safe)
            with yfinance_lock:
                # Add group_by='column' to ensure consistent column structure
                df = yf.download(ticker, period='max', interval='1d', progress=False, 
                               auto_adjust=False, timeout=10, threads=False, group_by='column')
            if df.empty:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                    logger.warning(f"No data returned for {ticker}, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"No data returned for {ticker} after {max_retries} attempts.")
                    return pd.DataFrame()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            break  # Success, exit retry loop
            
        except Exception as e:
            wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
            logger.warning(f"Attempt {attempt+1} failed for {ticker}: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error(f"All retries exhausted for {ticker}: {e}")
                return pd.DataFrame()
    
    try:
        # Use the helper function to handle all column structures
        df = _coerce_to_close_frame(df)
        if df.empty:
            logger.error(f"No Close/Adj Close data found for {ticker}, aborting this ticker.")
            return pd.DataFrame()

        # Apply the very same detector as onepass (parity)
        ticker_type = detect_ticker_type(ticker)
        if not is_session_complete(df, ticker_type):
            logger.debug(f"Dropping incomplete session for {ticker}")
            df = df.iloc[:-1]
        
        # NEW: Apply strict parity transform if enabled (e.g., rounding/normalization)
        df = apply_strict_parity(df)
        
        # Don't cache for impact analysis to avoid multiprocessing corruption
        # CacheManager.save_to_cache(df, ticker, 'data')
        
        logger.info(f"Successfully fetched {len(df)} days of data for {ticker}.")
        return df
    except Exception as e:
        logger.error(f"Error fetching data for {ticker}: {str(e)}")
        return pd.DataFrame()

def calculate_metrics_from_signals(primary_signals, primary_dates, secondary_df):
    logger.debug("Calculating final metrics from generated signals...")
    
    # Extra prints to debug alignment issues
    logger.debug(f"Initial primary_signals length: {len(primary_signals)}")
    logger.debug(f"Initial primary_dates range: {primary_dates[0]} to {primary_dates[-1]} (len={len(primary_dates)})")
    logger.debug(f"secondary_df index range: {secondary_df.index[0]} to {secondary_df.index[-1]} (len={len(secondary_df)})")
    
    # Guard against length mismatches (parity with onepass)
    if len(primary_signals) != len(primary_dates):
        n = min(len(primary_signals), len(primary_dates))
        logger.warning(f"Signal/date length mismatch: signals={len(primary_signals)}, dates={len(primary_dates)}. Truncating to {n}.")
        signals = pd.Series(primary_signals[:n], index=primary_dates[:n])
    else:
        signals = pd.Series(primary_signals, index=primary_dates)

    logger.debug(f"Signals head (unshifted):\n{signals.head(5)}")
    logger.debug(f"Signals tail (unshifted):\n{signals.tail(5)}")
    
    # Get common dates between signals and prices
    common_dates = sorted(set(primary_dates) & set(secondary_df.index))
    logger.debug(f"Number of common dates between signals & secondary: {len(common_dates)}")

    if len(common_dates) < 2:  # Only need 2 days minimum for valid calculations
        logger.debug("Insufficient overlapping dates for metrics calculation.")
        return None
        
    # Use all valid dates - MAX_SMA_DAY is just for SMA calculations
    valid_dates = common_dates
    
    # Now reindex both series to valid dates
    signals = signals.reindex(valid_dates).fillna('None')
    prices = secondary_df['Close'].reindex(valid_dates)
    
    logger.debug(f"Signals head after reindex:\n{signals.head(5)}")
    logger.debug(f"Prices head after reindex:\n{prices.head(5)}")
    
    # Returns are calculated after date alignment but before signal processing
    daily_returns = prices.pct_change()
    
    logger.debug(f"Daily returns head (pre-shift alignment):\n{daily_returns.head(5)}")
    
    # NEW: Drop days where pct_change is NaN (first aligned day) - PARITY CRITICAL
    valid = daily_returns.notna()
    signals = signals.loc[valid]
    daily_returns = daily_returns.loc[valid]
    
    logger.debug(f"After dropping NaN returns: {len(signals)} signals remaining")
    logger.debug(f"Signals head (NO SHIFT):\n{signals.head(5)}")

    logger.debug(f"Signals index after dropping first date: {signals.index[0]} ... {signals.index[-1]}")
    logger.debug(f"Daily returns index after dropping first date: {daily_returns.index[0]} ... {daily_returns.index[-1]}")
    
    # Clean signals and create masks
    signals = signals.fillna('None').str.strip()  # Ensure no NaN values
    buy_mask = signals.eq('Buy')
    short_mask = signals.eq('Short')
    trigger_mask = buy_mask | short_mask
    trigger_days = int(trigger_mask.sum())

    logger.debug(f"Final signals distribution:\n{signals.value_counts()}")
    logger.debug(f"Number of trigger days: {trigger_days}")

    if trigger_days == 0:
        logger.debug("No trigger days found, no metrics to report.")
        return None

    # Calculate captures for trigger days
    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask] * 100
    daily_captures.loc[short_mask] = -daily_returns.loc[short_mask] * 100

    # Get captures only for trigger days
    signal_captures = daily_captures[trigger_mask]

    logger.debug(f"Sample of signal_captures:\n{signal_captures.head(10)}\n...\n{signal_captures.tail(10)}")

    # Defensive math: explicitly drop NaN values (redundant after Patch #1, but safe)
    signal_captures = signal_captures.dropna()
    
    # Calculate basic metrics
    wins = (signal_captures > 0).sum()
    losses = trigger_days - wins
    win_ratio = (wins / trigger_days * 100)
    avg_daily_capture = signal_captures.mean()
    total_capture = signal_captures.sum()
    
    logger.debug(f"wins={wins}, losses={losses}, win_ratio={win_ratio:.2f}%")
    logger.debug(f"avg_daily_capture={avg_daily_capture:.4f}%, total_capture={total_capture:.4f}%")
    
    # Calculate standard deviation using ddof=1 for sample standard deviation
    if trigger_days > 1:
        std_dev = signal_captures.std(ddof=1)
        
        # Calculate Sharpe ratio
        risk_free_rate = 5.0  # 5% annual rate
        annualized_return = avg_daily_capture * 252
        annualized_std = std_dev * np.sqrt(252)
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std if annualized_std != 0 else 0.0
        # Ensure Sharpe ratio is real
        if isinstance(sharpe_ratio, complex):
            sharpe_ratio = sharpe_ratio.real
        
        # Calculate t-statistic and p-value
        if std_dev == 0:
            t_statistic, p_value = None, None
        else:
            t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days))
            p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=trigger_days - 1))
    else:
        std_dev = 0.0
        sharpe_ratio = 0.0
        t_statistic = None
        p_value = None

    # Determine significance levels
    significant_90 = 'Yes' if p_value is not None and p_value < 0.10 else 'No'
    significant_95 = 'Yes' if p_value is not None and p_value < 0.05 else 'No'
    significant_99 = 'Yes' if p_value is not None and p_value < 0.01 else 'No'

    logger.debug(
        f"Metrics:\n"
        f"  Trigger Days={trigger_days}\n"
        f"  Wins={wins}\n"
        f"  Losses={losses}\n"
        f"  Win Ratio={win_ratio:.2f}%\n"
        f"  StdDev={std_dev:.4f}%\n"
        f"  Sharpe Ratio={sharpe_ratio:.2f}\n"
        f"  Avg Daily Capture={avg_daily_capture:.4f}%\n"
        f"  Total Capture={total_capture:.4f}%\n"
        f"  t-Statistic={t_statistic if t_statistic is not None else 'N/A'}\n"
        f"  p-Value={p_value if p_value is not None else 'N/A'}"
    )

    return {
        'Primary Ticker': '',  # Will be filled later
        'Trigger Days': trigger_days,
        'Wins': int(wins),
        'Losses': int(losses),
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std_dev, 4),
        'Sharpe Ratio': round(sharpe_ratio, 2),
        'Avg Daily Capture (%)': round(avg_daily_capture, 4),
        'Total Capture (%)': round(total_capture, 4),
        't-Statistic': round(t_statistic, 4) if t_statistic is not None else 'N/A',
        'p-Value': round(p_value, 4) if p_value is not None else 'N/A',
        'Significant 90%': significant_90,
        'Significant 95%': significant_95,
        'Significant 99%': significant_99
    }

def export_results_to_excel(output_filename, metrics_list):
    # Ensure output directory exists
    output_dir = os.path.dirname(output_filename)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Exporting results to {output_filename}...")

    # Define your desired column order
    desired_order = [
        'Primary Ticker',
        'Trigger Days',
        'Wins',
        'Losses',
        'Win Ratio (%)',
        'Std Dev (%)',
        'Sharpe Ratio',
        't-Statistic',
        'p-Value',
        'Significant 90%',
        'Significant 95%',
        'Significant 99%',
        'Avg Daily Capture (%)',
        'Total Capture (%)'
    ]

    if os.path.exists(output_filename):
        existing_df = pd.read_excel(output_filename)
        new_df = pd.DataFrame(metrics_list)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # Optionally sort by Sharpe Ratio
        if 'Sharpe Ratio' in combined_df.columns:
            combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        # Reorder columns if they exist
        for col in desired_order:
            if col not in combined_df.columns:
                combined_df[col] = np.nan
        combined_df = combined_df[[col for col in desired_order if col in combined_df.columns]]

        combined_df.to_excel(output_filename, index=False)
    else:
        df = pd.DataFrame(metrics_list)

        # Optionally sort by Sharpe Ratio
        if 'Sharpe Ratio' in df.columns:
            df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        # Reorder columns if they exist
        for col in desired_order:
            if col not in df.columns:
                df[col] = np.nan
        df = df[[col for col in desired_order if col in df.columns]]

        df.to_excel(output_filename, index=False)

    logger.info("Results successfully exported.")

def process_single_ticker_wrapper(args):
    """Wrapper for multiprocessing compatibility"""
    return process_single_ticker(*args)

def process_single_ticker(prim_ticker, sec_df, sma_cache=None):
    """Process a single primary ticker"""
    prim_ticker = normalize_ticker(prim_ticker)
    logger.info(f"Processing {prim_ticker}...")
    
    # If Signal Library is available, use precomputed signals
    primary_signals = None
    primary_dates = None
    
    # Try to load Signal Library first for massive speedup
    signal_data = load_signal_library(prim_ticker)
    
    # Always fetch fresh data for primary tickers (no caching to avoid corruption)
    df = fetch_data(prim_ticker, use_cache=False)
    if df.empty:
        logger.warning(f"No data for primary ticker {prim_ticker}, skipping.")
        return None

    close_values = df['Close'].values
    num_days = len(df)
    if num_days < 2:
        logger.warning(f"Insufficient days of data for {prim_ticker}, skipping.")
        return None
    
    # Debug: Verify unique data per ticker
    logger.info(f"Ticker {prim_ticker}: {num_days} days, Close[0]={close_values[0]:.2f}, Close[-1]={close_values[-1]:.2f}")

    # If Signal Library is available, use precomputed signals
    if signal_data:
        # Evaluate library acceptance using multi-tier ladder
        acceptance_level, integrity_status, message = evaluate_library_acceptance(signal_data, df)
        
        logger.info(f"Signal Library acceptance for {prim_ticker}: {acceptance_level} - {message}")
        
        # Log parity status if configured
        if LOG_ACCEPTANCE_TIER and acceptance_level != 'STRICT':
            logger.debug(f"  Acceptance tier: {acceptance_level}, Integrity: {integrity_status}")
        
        # Only rebuild if absolutely necessary
        if acceptance_level == 'REBUILD':
            logger.warning(f"Signal Library rebuild required for {prim_ticker}: {message}")
            signal_data = None
        else:
            # Accept the library under all other tiers
            if acceptance_level != 'STRICT':
                logger.info(f"Accepting Signal Library with {acceptance_level} match - {message}")
            
            # Phase 2: Handle incremental updates
            if integrity_status == 'NEW_DATA':
                logger.info(f"New data available for {prim_ticker} but library still usable")
                # Check if library was incrementally updated
                if signal_data.get('incremental_update'):
                    logger.info(f"Signal Library was incrementally updated by onepass.py")
                else:
                    logger.info(f"Consider running onepass.py to append new days")
        
    if signal_data and 'primary_signals' in signal_data:
        # BEST CASE: Use pre-computed primary_signals directly!
        logger.info(f"Using enhanced Signal Library V2 for {prim_ticker} - ULTIMATE SPEEDUP!")
        primary_signals = signal_data['primary_signals']
        daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
        daily_top_short_pairs = signal_data['daily_top_short_pairs']
        
        # Align signals with current data - O(N) using dict
        if 'dates' in signal_data:
            stored_dates = signal_data['dates']
            primary_dates = df.index
            
            # Build dict once for O(1) lookups - fixes O(N²) issue
            signal_map = {date: signal for date, signal in zip(stored_dates, primary_signals)}
            
            # Map signals in O(N) total time
            primary_signals_aligned = []
            for date in primary_dates:
                date_str = str(date.date())
                primary_signals_aligned.append(signal_map.get(date_str, 'None'))
            
            primary_signals = primary_signals_aligned
            
            # NO SMA COMPUTATION NEEDED AT ALL!
            logger.info(f"Skipping ALL SMA computation - using {len(primary_signals)} pre-computed signals")
            
            # Jump directly to metrics calculation
            logger.info("Calculating metrics from pre-computed signals...")
            # The rest of the function will handle metrics calculation
            sma_matrix = None  # We don't need it!
        else:
            # Fallback if dates not available
            logger.warning("Signal Library V2 missing dates - falling back to regular processing")
            primary_signals = None
            signal_data = None
    
    elif signal_data and primary_signals is None:
        # V1 format - has daily pairs but not primary_signals
        logger.info(f"Using Signal Library V1 for {prim_ticker} - partial speedup")
        daily_top_buy_pairs = signal_data['daily_top_buy_pairs']
        daily_top_short_pairs = signal_data['daily_top_short_pairs']
        
        # Normalize V1 library keys to Timestamp (future-proof for string keys)
        def _normalize_pair_keys_to_timestamp(d):
            out = {}
            for k, v in d.items():
                try:
                    kt = pd.Timestamp(k) if not isinstance(k, pd.Timestamp) else k
                except Exception:
                    kt = k
                out[kt] = v
            return out
        
        daily_top_buy_pairs = _normalize_pair_keys_to_timestamp(daily_top_buy_pairs)
        daily_top_short_pairs = _normalize_pair_keys_to_timestamp(daily_top_short_pairs)
        
        # Still need SMA matrix for signal derivation
        cache_key = f"{prim_ticker}_sma"
        if sma_cache and cache_key in sma_cache:
            sma_matrix = sma_cache[cache_key]
            logger.debug(f"Using cached SMA for {prim_ticker}")
        else:
            logger.info("Computing SMAs for signal derivation (V1 format)...")
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float32)
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, num_days)
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            if sma_cache is not None:
                sma_cache[cache_key] = sma_matrix
    else:
        # No Signal Library - compute from scratch
        logger.info(f"No Signal Library found for {prim_ticker}, computing from scratch...")
        
        # Check for cached SMA calculations
        cache_key = f"{prim_ticker}_sma"
        if sma_cache and cache_key in sma_cache:
            sma_matrix = sma_cache[cache_key]
            logger.debug(f"Using cached SMA for {prim_ticker}")
        else:
            logger.info("Computing SMAs...")
            cumsum = np.cumsum(np.insert(close_values, 0, 0))
            sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=np.float32)
            sma_matrix.fill(np.nan)
            for i in range(1, MAX_SMA_DAY + 1):
                valid_indices = np.arange(i-1, num_days)
                sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i
            
            if sma_cache is not None:
                sma_cache[cache_key] = sma_matrix

        # Compute returns once (converted to float32 for efficiency)
        logger.info("Computing returns using pct_change()...")
        returns_pct = df['Close'].pct_change().fillna(0).to_numpy(dtype=np.float32) * 100
        
        logger.info("Computing daily top pairs using fully-streaming algorithm...")
        # True streaming: no O(days × pairs) arrays at all
        daily_top_buy_pairs = {}
        daily_top_short_pairs = {}
        
        # Use float64 for accumulators to prevent precision loss over long periods
        buy_cum = np.zeros(len(PAIRS), dtype=np.float64)
        short_cum = np.zeros(len(PAIRS), dtype=np.float64)
        
        for idx, date in enumerate(df.index):
            # Skip first day - can't trade without previous day's signals
            if idx == 0:
                daily_top_buy_pairs[date] = ((1, 2), 0.0)
                daily_top_short_pairs[date] = ((1, 2), 0.0)
                continue
            
            # Use PREVIOUS day's SMAs to generate signals
            sma_t_prev = sma_matrix[idx - 1]  # Yesterday's SMAs
            
            # Compute signals based on yesterday's SMAs
            valid_mask = np.isfinite(sma_t_prev[I_IDX]) & np.isfinite(sma_t_prev[J_IDX])
            cmp = np.zeros(len(PAIRS), dtype=np.int8)
            cmp[valid_mask] = np.sign(sma_t_prev[I_IDX[valid_mask]] - sma_t_prev[J_IDX[valid_mask]]).astype(np.int8)
            
            # Apply to TODAY's return
            r = float(returns_pct[idx])
            
            # Update cumulative captures
            if r != 0.0:
                buy_mask = (cmp == 1)
                if buy_mask.any():
                    buy_cum[buy_mask] += r
                
                short_mask = (cmp == -1)
                if short_mask.any():
                    short_cum[short_mask] += -r  # Gain from shorting = negative of market return
            
            # Find top pairs with reverse tie-breaking
            max_buy_idx = len(buy_cum) - 1 - np.argmax(buy_cum[::-1])
            max_short_idx = len(short_cum) - 1 - np.argmax(short_cum[::-1])
            
            # Store results
            daily_top_buy_pairs[date] = (
                (int(PAIRS[max_buy_idx, 0]), int(PAIRS[max_buy_idx, 1])),
                float(buy_cum[max_buy_idx])
            )
            daily_top_short_pairs[date] = (
                (int(PAIRS[max_short_idx, 0]), int(PAIRS[max_short_idx, 1])),
                float(short_cum[max_short_idx])
            )

    # Derive signals if we still don't have them pre-computed
    if primary_signals is None:
        # Need to derive signals - we don't have them pre-computed
        logger.info("Deriving primary signals from previous day's top pairs...")
        primary_dates = df.index
        primary_signals = []
        previous_date = None

        for date in primary_dates:
            if previous_date is None:
                primary_signals.append('None')
                previous_date = date
                continue

            buy_pair, buy_val = daily_top_buy_pairs.get(previous_date, ((1,2),0.0))
            short_pair, short_val = daily_top_short_pairs.get(previous_date, ((1,2),0.0))

            # Get previous day's SMA values
            sma1_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[0]-1]
            sma2_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[1]-1]
            sma1_short = sma_matrix[df.index.get_loc(previous_date), short_pair[0]-1]
            sma2_short = sma_matrix[df.index.get_loc(previous_date), short_pair[1]-1]

            buy_signal = sma1_buy > sma2_buy
            short_signal = sma1_short < sma2_short

            if buy_signal and short_signal:
                current_signal = get_tiebreak_signal(buy_val, short_val)
            elif buy_signal:
                current_signal = 'Buy'
            elif short_signal:
                current_signal = 'Short'
            else:
                current_signal = 'None'

            primary_signals.append(current_signal)
            previous_date = date
        
        # Memory hygiene: release SMA matrix after signal derivation
        if 'sma_matrix' in locals():
            del sma_matrix
            logger.debug("Released SMA matrix memory")
    else:
        # We already have pre-computed signals (V2 path)
        logger.info(f"Using {len(primary_signals)} pre-computed signals from Signal Library V2")
        primary_dates = df.index

    logger.info("Calculating final metrics for this primary ticker...")
    logger.info(f"Signal distribution before metrics calculation:")
    signal_counts = pd.Series(primary_signals).value_counts()
    logger.info(f"Buy signals: {signal_counts.get('Buy', 0)}")
    logger.info(f"Short signals: {signal_counts.get('Short', 0)}")
    logger.info(f"None signals: {signal_counts.get('None', 0)}")
    
    result = calculate_metrics_from_signals(primary_signals, primary_dates, sec_df)
    if result is not None:
        result['Primary Ticker'] = prim_ticker
    
    return result

def process_primary_tickers(secondary_ticker, primary_tickers, use_multiprocessing=False):
    """Process primary tickers with progress tracking and optional multiprocessing"""
    global progress_tracker
    
    # Deduplicate primary tickers after normalization
    primary_tickers = deduplicate_tickers(primary_tickers)
    
    secondary_ticker = normalize_ticker(secondary_ticker)
    sec_df = fetch_data(secondary_ticker, use_cache=False)
    if sec_df.empty:
        logger.error(f"No data for secondary ticker {secondary_ticker}, cannot proceed.")
        return []
        
    # Ensure proper data alignment from the start
    sec_df = sec_df.sort_index()

    metrics_list = []
    sma_cache = {}  # Cache for SMA calculations

    logger.info(f"Starting analysis for Secondary Ticker: {secondary_ticker}")
    
    # Update progress tracker
    progress_tracker['total_tickers'] = len(primary_tickers)
    progress_tracker['start_time'] = time.time()
    progress_tracker['status'] = 'processing'
    
    if use_multiprocessing and len(primary_tickers) > 3:
        # Use multiprocessing for large batches
        logger.info("Using multiprocessing for faster analysis...")
        
        # Pass sec_df by reference (read-only in workers, no need to copy)
        process_args = [(ticker, sec_df, None) for ticker in primary_tickers]
        
        # Use ThreadPoolExecutor (ProcessPoolExecutor has pickle issues with DataFrames)
        max_workers = max(1, min(multiprocessing.cpu_count() - 1, 8))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks with isolated data copies
            futures = {executor.submit(process_single_ticker, *args): args[0] 
                      for args in process_args}
            
            completed_count = 0
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()  # No timeout
                    if result:
                        result['Secondary Ticker'] = secondary_ticker
                        metrics_list.append(result)
                        progress_tracker['results'] = metrics_list.copy()
                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                finally:
                    completed_count += 1
                    progress_tracker['current_index'] = completed_count  # Not -1
                    progress_tracker['current_ticker'] = ticker
                    logger.info(f"Completed {completed_count}/{len(primary_tickers)}: {ticker}")
    else:
        # Sequential processing for small batches (with TQDM console bar)
        for idx, prim_ticker in enumerate(tqdm(primary_tickers, desc="Processing Primary Tickers", unit="ticker")):
            with progress_lock:
                progress_tracker['current_ticker'] = prim_ticker
            
            result = process_single_ticker(prim_ticker, sec_df, sma_cache)
            if result:
                result['Secondary Ticker'] = secondary_ticker
                metrics_list.append(result)
                progress_tracker['results'] = metrics_list.copy()
            
            progress_tracker['current_index'] = idx + 1  # Mark as completed
            logger.info(f"Completed {idx+1}/{len(primary_tickers)}: {prim_ticker}")

    progress_tracker['status'] = 'complete'
    return metrics_list

# Create Dash app
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

# Define the layout
app.layout = dbc.Container([
    # Header
    dbc.Row([
        dbc.Col([
            html.H1("Impact Analysis Tool", 
                   style={'color': '#00ff41', 'textShadow': '0 0 10px rgba(0, 255, 65, 0.5)'}),
            html.P("Analyze the impact of primary tickers on secondary ticker performance using SMA-based signals",
                  style={'color': '#aaa'})
        ])
    ], className='mb-4'),
    
    # Input Section
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("Analysis Configuration", style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.Label("Primary Tickers", style={'color': '#00ff41'}),
                            html.P("Enter comma-separated tickers (e.g., AAPL, MSFT, GOOGL)", 
                                  style={'fontSize': '0.8rem', 'color': '#888'}),
                            dbc.Textarea(
                                id='primary-tickers-input',
                                placeholder='Enter primary tickers...',
                                style={'height': '100px', 'backgroundColor': 'rgba(0, 0, 0, 0.3)',
                                      'border': '1px solid #00ff41', 'color': '#fff'},
                                className='mb-3'
                            ),
                            # Preset ticker lists - consistent styling
                            dbc.ButtonGroup([
                                dbc.Button("Tech Giants", id='preset-tech', color='primary', size='sm', outline=True),
                                dbc.Button("S&P Top 10", id='preset-sp10', color='primary', size='sm', outline=True),
                                dbc.Button("Crypto Top 10", id='preset-crypto', color='success', size='sm', outline=True),
                                dbc.Button("Random Mix (20)", id='preset-random', color='info', size='sm'),
                                dbc.Button("Clear", id='preset-clear', color='danger', size='sm'),
                                dbc.Button("Clear Cache", id='clear-cache-btn', color='warning', size='sm')
                            ], className='mb-2'),
                            
                            # Market cap category presets - consistent styling
                            dbc.ButtonGroup([
                                dbc.Button("Mega Cap ($200B+)", id='preset-mega', color='primary', size='sm', outline=True),
                                dbc.Button("Large Cap ($10-200B)", id='preset-large', color='primary', size='sm', outline=True),
                                dbc.Button("Mid Cap ($2-10B)", id='preset-mid', color='primary', size='sm', outline=True),
                                dbc.Button("Small Cap ($300M-2B)", id='preset-small', color='primary', size='sm', outline=True),
                                dbc.Button("Micro Cap (<$300M)", id='preset-micro', color='primary', size='sm', outline=True)
                            ], className='mb-3'),
                            
                            # File upload
                            html.Hr(style={'borderColor': '#444'}),
                            html.Label("Or Upload Ticker List", style={'color': '#00ff41', 'fontSize': '0.9rem'}),
                            dcc.Upload(
                                id='upload-tickers',
                                children=html.Div([
                                    'Drag and Drop or ',
                                    html.A('Select CSV/TXT File', style={'color': '#00ff41', 'textDecoration': 'underline'})
                                ]),
                                style={
                                    'width': '100%',
                                    'height': '60px',
                                    'lineHeight': '60px',
                                    'borderWidth': '1px',
                                    'borderStyle': 'dashed',
                                    'borderRadius': '5px',
                                    'borderColor': '#00ff41',
                                    'textAlign': 'center',
                                    'margin': '10px 0',
                                    'backgroundColor': 'rgba(0, 255, 65, 0.05)'
                                },
                                multiple=False
                            )
                        ], width=6),
                        dbc.Col([
                            html.Label("Secondary Ticker", style={'color': '#00ff41'}),
                            html.P("Enter the ticker to analyze impact against (e.g., SPY)", 
                                  style={'fontSize': '0.8rem', 'color': '#888'}),
                            dbc.Input(
                                id='secondary-ticker-input',
                                placeholder='Enter secondary ticker...',
                                type='text',
                                style={'backgroundColor': 'rgba(0, 0, 0, 0.3)',
                                      'border': '1px solid #00ff41', 'color': '#fff'},
                                className='mb-3'
                            ),
                            html.Hr(style={'borderColor': '#444', 'marginTop': '20px'}),
                            html.Label("Analysis Options", style={'color': '#00ff41', 'fontSize': '0.9rem'}),
                            dbc.Checklist(
                                id='analysis-options',
                                options=[
                                    {'label': ' Use Multiprocessing (Faster for >3 tickers)', 'value': 'multiprocessing'},
                                    {'label': ' Export Excel File', 'value': 'export_excel'},
                                    {'label': ' Generate PDF Report' + (' (Requires ReportLab)' if not REPORTLAB_AVAILABLE else ''), 
                                     'value': 'pdf', 'disabled': not REPORTLAB_AVAILABLE},
                                    {'label': ' Save as Template', 'value': 'save_template'}
                                ],
                                value=['multiprocessing', 'export_excel'],
                                inline=False,
                                style={'color': '#aaa', 'fontSize': '0.85rem'}
                            ),
                            dbc.Button(
                                "Start Analysis",
                                id='process-button',
                                color='success',
                                size='lg',
                                style={'width': '100%', 'marginTop': '20px'},
                                className='pulse-animation'
                            )
                        ], width=6)
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41'})
        ])
    ], className='mb-4'),
    
    # Progress Section
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.Div(id='progress-section', children=[
                        html.H5("Ready to analyze", style={'color': '#00ff41'}),
                        dbc.Progress(value=0, id='progress-bar', striped=True, animated=True, 
                                   style={'height': '30px'}, color='success')
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #444'})
        ])
    ], className='mb-4', id='progress-row', style={'display': 'none'}),
    
    # Summary Cards Row
    dbc.Row([
        dbc.Col([html.Div(id='summary-cards')], width=12)
    ], className='mb-4'),
    
    # Results Section with Tabs
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("Analysis Results", style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Tabs([
                        dbc.Tab(label="📋 Summary", tab_id="tab-summary", 
                               label_style={'color': '#00ff41', 'fontWeight': 'bold'}),
                        dbc.Tab(label="Results Table", tab_id="tab-table"),
                        dbc.Tab(label="Performance Charts", tab_id="tab-charts"),
                        dbc.Tab(label="Statistical Analysis", tab_id="tab-stats"),
                        dbc.Tab(label="Correlation Analysis", tab_id="tab-correlation"),
                        dbc.Tab(label="Advanced Analytics", tab_id="tab-advanced")
                    ], id='result-tabs', active_tab='tab-summary'),
                    html.Div(id='tab-content', className='mt-3')
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41',
                     'display': 'none'}, id='results-card')
        ])
    ]),
    
    # Interval component for real-time updates
    dcc.Interval(id='interval-component', interval=1000, n_intervals=0, disabled=True),
    
    # Store components for data persistence
    dcc.Store(id='analysis-results-store'),
    dcc.Store(id='processing-state-store', data={'status': 'idle'}),
    dcc.Store(id='follow-up-action-store'),
    dcc.Store(id='secondary-ticker-store')
    
], fluid=True, style={'backgroundColor': '#0a0a0a', 'minHeight': '100vh', 'padding': '20px'})

# File upload callback
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    [Input('upload-tickers', 'contents')],
    [State('upload-tickers', 'filename')],
    prevent_initial_call=True
)
def parse_uploaded_file(contents, filename):
    if contents is None:
        raise dash.exceptions.PreventUpdate
    
    try:
        content_type, content_string = contents.split(',')
        decoded = base64.b64decode(content_string)
        
        # Try to decode as text
        try:
            text_content = decoded.decode('utf-8')
        except:
            text_content = decoded.decode('latin-1')
        
        # Parse tickers from the content
        # Handle both CSV and plain text formats
        tickers = []
        
        if filename.endswith('.csv'):
            # Parse as CSV
            df = pd.read_csv(io.StringIO(text_content))
            # Look for a column that might contain tickers
            for col in df.columns:
                if 'ticker' in col.lower() or 'symbol' in col.lower() or col.lower() == 'ticker':
                    tickers = df[col].dropna().tolist()
                    break
            if not tickers and len(df.columns) > 0:
                # Use first column if no ticker column found
                tickers = df.iloc[:, 0].dropna().tolist()
        else:
            # Parse as plain text (comma or newline separated)
            # Replace common separators with commas
            text_content = text_content.replace('\n', ',').replace('\r', ',').replace(';', ',')
            tickers = [t.strip() for t in text_content.split(',') if t.strip()]
        
        # Clean and validate tickers
        tickers = [t.upper().strip() for t in tickers if t.strip() and len(t.strip()) <= 10]
        
        if tickers:
            return ', '.join(tickers[:100])  # Limit to 100 tickers
        else:
            return dash.no_update
            
    except Exception as e:
        logger.error(f"Error parsing uploaded file: {e}")
        return dash.no_update

# Define market cap category ticker lists
MEGA_CAP_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'LLY', 'TSM', 'V',
                    'JPM', 'WMT', 'JNJ', 'XOM', 'UNH', 'MA', 'PG', 'HD', 'CVX', 'MRK']

LARGE_CAP_TICKERS = ['CRM', 'AMD', 'ORCL', 'NFLX', 'COST', 'PEP', 'KO', 'BA', 'GS', 'IBM',
                     'INTC', 'DIS', 'CSCO', 'TMO', 'ABT', 'VZ', 'NKE', 'WFC', 'MS', 'QCOM']

MID_CAP_TICKERS = ['SNAP', 'ROKU', 'ZM', 'PINS', 'PLTR', 'DOCU', 'TWLO', 'NET', 'DDOG', 'CRWD',
                   'PATH', 'U', 'RBLX', 'COIN', 'HOOD', 'AFRM', 'SOFI', 'UPST', 'BILL', 'MARA']

SMALL_CAP_TICKERS = ['FSLY', 'FVRR', 'APPS', 'FUBO', 'SKLZ', 'VERI', 'SPCE', 'RKT', 'OPEN', 'ASTS',
                     'CLOV', 'STEM', 'GOEV', 'WKHS', 'HYLN', 'CHPT', 'BLNK', 'EVGO', 'QS', 'PAYO']

MICRO_CAP_TICKERS = ['TBLT', 'SYTA', 'PRPO', 'EDBL', 'SOUN', 'PETZ', 'TKLF', 'MBOT', 'GMBL', 'ACHR',
                     'GEVO', 'DAVE', 'SNGX', 'BTAI', 'BTBT', 'IONQ', 'NUKK', 'ADTX', 'BOXL', 'VERB']

# Popular crypto tickers for analysis
CRYPTO_TICKERS = ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 
                  'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD',
                  'LINK-USD', 'LTC-USD', 'UNI-USD', 'ATOM-USD', 'ETC-USD']

def get_random_mix():
    """Generate a random mix of 20 tickers from all categories"""
    all_categories = [
        MEGA_CAP_TICKERS,
        LARGE_CAP_TICKERS,
        MID_CAP_TICKERS,
        SMALL_CAP_TICKERS,
        MICRO_CAP_TICKERS
    ]
    
    selected = []
    for category in all_categories:
        # Select 4 tickers from each category
        selected.extend(random.sample(category, min(4, len(category))))
    
    # Shuffle the selected tickers
    random.shuffle(selected)
    return selected[:20]

# Callback for clearing cache
@app.callback(
    Output('progress-section', 'children', allow_duplicate=True),
    Input('clear-cache-btn', 'n_clicks'),
    prevent_initial_call=True
)
def clear_cache(n_clicks):
    if n_clicks:
        import shutil
        cache_cleared = False
        
        # Clear the cache/impact_analysis directory
        impact_cache_dir = CACHE_DIR  # CACHE_DIR already == 'cache/impact_analysis'
        if os.path.exists(impact_cache_dir):
            try:
                shutil.rmtree(impact_cache_dir)
                os.makedirs(impact_cache_dir, exist_ok=True)
                cache_cleared = True
                logger.info("Cache cleared successfully")
            except Exception as e:
                logger.error(f"Failed to clear cache: {e}")
                return html.Div([
                    html.H5("Failed to clear cache", style={'color': '#ff4141'}),
                    dbc.Progress(value=0, striped=True, animated=True, style={'height': '30px'}, color='danger')
                ])
        
        if cache_cleared:
            return html.Div([
                html.H5("Cache cleared successfully! Ready to analyze", style={'color': '#00ff41'}),
                dbc.Progress(value=0, striped=True, animated=True, style={'height': '30px'}, color='success')
            ])
    
    raise dash.exceptions.PreventUpdate

# Callbacks for preset buttons
@app.callback(
    Output('primary-tickers-input', 'value', allow_duplicate=True),
    [Input('preset-tech', 'n_clicks'),
     Input('preset-sp10', 'n_clicks'),
     Input('preset-crypto', 'n_clicks'),
     Input('preset-clear', 'n_clicks'),
     Input('preset-mega', 'n_clicks'),
     Input('preset-large', 'n_clicks'),
     Input('preset-mid', 'n_clicks'),
     Input('preset-small', 'n_clicks'),
     Input('preset-micro', 'n_clicks'),
     Input('preset-random', 'n_clicks')],
    [State('primary-tickers-input', 'value')],
    prevent_initial_call=True
)
def handle_presets(tech_clicks, sp10_clicks, crypto_clicks, clear_clicks, mega_clicks, large_clicks, 
                  mid_clicks, small_clicks, micro_clicks, random_clicks, current_value):
    ctx = callback_context
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Define preset lists
    preset_lists = {
        'preset-tech': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA'],
        'preset-sp10': ['AAPL', 'MSFT', 'AMZN', 'NVDA', 'GOOGL', 'META', 'BRK-B', 'LLY', 'AVGO', 'JPM'],
        'preset-crypto': ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 
                         'ADA-USD', 'DOGE-USD', 'AVAX-USD', 'DOT-USD', 'MATIC-USD'],
        'preset-mega': MEGA_CAP_TICKERS,
        'preset-large': LARGE_CAP_TICKERS,
        'preset-mid': MID_CAP_TICKERS,
        'preset-small': SMALL_CAP_TICKERS,
        'preset-micro': MICRO_CAP_TICKERS,
        'preset-random': get_random_mix()
    }
    
    # Handle clear button
    if button_id == 'preset-clear':
        return ''
    
    # Get the new tickers to add
    if button_id in preset_lists:
        new_tickers = preset_lists[button_id]
        
        # Parse existing tickers
        existing_tickers = []
        if current_value:
            existing_tickers = [t.strip().upper() for t in current_value.split(',') if t.strip()]
        
        # Combine and deduplicate (preserving order, new tickers at end)
        combined_tickers = existing_tickers.copy()
        for ticker in new_tickers:
            if ticker.upper() not in [t.upper() for t in combined_tickers]:
                combined_tickers.append(ticker)
        
        return ', '.join(combined_tickers)
    
    raise dash.exceptions.PreventUpdate

# Main processing callback
@app.callback(
    [Output('interval-component', 'disabled'),
     Output('processing-state-store', 'data'),
     Output('progress-row', 'style'),
     Output('results-card', 'style'),
     Output('secondary-ticker-store', 'data')],
    [Input('process-button', 'n_clicks')],
    [State('primary-tickers-input', 'value'),
     State('secondary-ticker-input', 'value'),
     State('analysis-options', 'value')]
)
def start_processing(n_clicks, primary_tickers_input, secondary_ticker, analysis_options):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    
    if not secondary_ticker or not primary_tickers_input:
        raise dash.exceptions.PreventUpdate
    
    # Parse tickers
    primary_tickers = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]
    
    # Reset progress tracker
    global progress_tracker
    progress_tracker = {
        'current_ticker': '',
        'current_index': 0,
        'total_tickers': len(primary_tickers),
        'start_time': time.time(),
        'results': [],
        'status': 'starting'
    }
    
    # Determine options
    if analysis_options is None:
        analysis_options = []
    
    use_multiprocessing = 'multiprocessing' in analysis_options
    export_excel = 'export_excel' in analysis_options
    generate_pdf = 'pdf' in analysis_options
    save_template = 'save_template' in analysis_options
    
    # Start processing in a separate thread
    def process_async():
        results = process_primary_tickers(secondary_ticker, primary_tickers, use_multiprocessing)
        if results:
            # Export Excel if requested
            if export_excel:
                output_filename = f"output/impactsearch/{secondary_ticker}_analysis.xlsx"
                export_results_to_excel(output_filename, results)
                logger.info(f"Excel file exported to {output_filename}")
            
            # Generate PDF if requested
            if generate_pdf:
                results_df = pd.DataFrame(results)
                ReportGenerator.generate_pdf_report(results_df, secondary_ticker)
            
            # Save template if requested
            if save_template:
                template_config = {
                    'primary_tickers': primary_tickers,
                    'secondary_ticker': secondary_ticker,
                    'options': analysis_options,
                    'timestamp': datetime.now().isoformat()
                }
                template_name = f"{secondary_ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                AnalysisTemplates.save_template(template_name, template_config)
    
    thread = threading.Thread(target=process_async)
    thread.start()
    
    # Enable interval updates and show progress
    return False, {'status': 'processing'}, {'display': 'block'}, \
           {'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #00ff41', 'display': 'block'}, \
           secondary_ticker

# Progress update callback
@app.callback(
    [Output('progress-section', 'children'),
     Output('summary-cards', 'children'),
     Output('analysis-results-store', 'data'),
     Output('interval-component', 'disabled', allow_duplicate=True),
     Output('processing-state-store', 'data', allow_duplicate=True)],
    [Input('interval-component', 'n_intervals')],
    [State('processing-state-store', 'data')],
    prevent_initial_call=True
)
def update_progress(n_intervals, processing_state):
    global progress_tracker
    
    if not processing_state or processing_state.get('status') != 'processing':
        raise dash.exceptions.PreventUpdate
    
    # Calculate progress
    if progress_tracker['total_tickers'] > 0:
        done = min(progress_tracker['current_index'], progress_tracker['total_tickers'])
        progress_pct = (done / progress_tracker['total_tickers']) * 100
    else:
        progress_pct = 0
    
    # Estimate time remaining
    if progress_tracker['start_time'] and progress_tracker['current_index'] > 0:
        elapsed = time.time() - progress_tracker['start_time']
        rate = elapsed / progress_tracker['current_index']
        remaining = rate * (progress_tracker['total_tickers'] - progress_tracker['current_index'])
        time_str = f"~{int(remaining)}s remaining"
    else:
        time_str = "Calculating..."
    
    # Create progress display
    progress_display = html.Div([
        html.H5(f"Processing: {progress_tracker['current_ticker']}", style={'color': '#00ff41'}),
        html.P(f"Ticker {progress_tracker['current_index'] + 1} of {progress_tracker['total_tickers']} | {time_str}",
              style={'color': '#aaa'}),
        dbc.Progress(value=progress_pct, striped=True, animated=True, 
                    style={'height': '30px'}, color='success')
    ])
    
    # Create summary cards if we have results
    summary_cards = []
    if progress_tracker['results']:
        results_df = pd.DataFrame(progress_tracker['results'])
        
        # Calculate summary metrics
        avg_sharpe = results_df['Sharpe Ratio'].mean()
        best_performer = results_df.loc[results_df['Sharpe Ratio'].idxmax()]
        significant_count = len(results_df[results_df['Significant 95%'] == 'Yes'])
        
        summary_cards = dbc.Row([
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Analyzed", 
                    len(results_df),
                    f"of {progress_tracker['total_tickers']} tickers",
                    "📊", "#00ff41", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Avg Sharpe", 
                    f"{avg_sharpe:.2f}",
                    "Risk-adjusted return",
                    "📈", "#80ff00" if avg_sharpe > 0 else "#ff0040", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Best Performer", 
                    best_performer['Primary Ticker'],
                    f"Sharpe: {best_performer['Sharpe Ratio']:.2f}",
                    "🏆", "#00ff41", glow=True
                )
            ], width=3),
            dbc.Col([
                VisualMetrics.create_performance_card(
                    "Significant", 
                    significant_count,
                    "95% confidence level",
                    "✅", "#00ff41" if significant_count > 0 else "#ff0040", glow=True
                )
            ], width=3)
        ])
    
    # Check if processing is complete
    if progress_tracker['status'] == 'complete':
        progress_display = html.Div([
            html.H5("Analysis Complete! ✅", style={'color': '#00ff41'}),
            html.P(f"Processed {progress_tracker['total_tickers']} tickers successfully",
                  style={'color': '#aaa'}),
            dbc.Progress(value=100, striped=False, style={'height': '30px'}, color='success')
        ])
        # Stop the interval and update state when complete
        return progress_display, summary_cards, progress_tracker['results'], True, {'status': 'complete'}
    
    # Continue updating while processing
    return progress_display, summary_cards, progress_tracker['results'], False, processing_state

# Tab content callback
@app.callback(
    Output('tab-content', 'children'),
    [Input('result-tabs', 'active_tab'),
     Input('analysis-results-store', 'data')],
)
def render_tab_content(active_tab, results_data):
    if not results_data:
        return html.Div("No results to display yet.", style={'color': '#aaa'})
    
    df = pd.DataFrame(results_data)
    
    if active_tab == 'tab-summary':
        # Generate intelligent summary
        summary_content = []
        
        # Get secondary ticker from the first result (they all have the same)
        secondary_ticker = df.iloc[0]['Secondary Ticker'] if 'Secondary Ticker' in df.columns else 'N/A'
        
        # Title section
        summary_content.append(
            html.Div([
                html.H3("📊 Analysis Summary", style={'color': '#00ff41', 'marginBottom': '20px'}),
                html.P(f"Impact analysis of {len(df)} tickers against {secondary_ticker}", 
                      style={'color': '#aaa', 'fontSize': '1.1rem'})
            ])
        )
        
        # Key Findings Section
        findings = SummaryAnalyzer.analyze_key_findings(df)
        if findings:
            findings_cards = []
            for finding in findings:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(finding['title'], style={'color': '#00ff41', 'marginBottom': '10px'}),
                        html.P(finding['description'], style={'fontSize': '1rem', 'marginBottom': '5px'}),
                        html.P(finding['details'], style={'fontSize': '0.9rem', 'color': '#888'})
                    ])
                ], style={'backgroundColor': 'rgba(0, 0, 0, 0.4)', 'border': '1px solid #444', 
                         'marginBottom': '15px'})
                findings_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🎯 Key Findings", style={'color': '#80ff00', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div(findings_cards)
            ]))
        
        # Pattern Detection Section
        patterns = SummaryAnalyzer.detect_patterns(df)
        if patterns:
            pattern_cards = []
            for pattern in patterns:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(pattern['title'], style={'color': '#ffff00', 'marginBottom': '10px'}),
                        html.P(pattern['description'], style={'fontSize': '1rem', 'marginBottom': '5px'}),
                        html.P(f"💡 {pattern['recommendation']}", 
                              style={'fontSize': '0.9rem', 'color': '#00ff41', 'fontStyle': 'italic'})
                    ])
                ], style={'backgroundColor': 'rgba(255, 255, 0, 0.05)', 'border': '1px solid #ffff00', 
                         'marginBottom': '15px'})
                pattern_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🔍 Detected Patterns", style={'color': '#ffff00', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div(pattern_cards)
            ]))
        
        # Summary Visualizations
        visualizations = SummaryAnalyzer.create_summary_visualizations(df)
        if visualizations:
            summary_content.append(html.Div([
                html.H4("📈 Visual Summary", style={'color': '#00ff41', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.Div([dcc.Graph(figure=fig, config={'displayModeBar': False}) 
                         for _, fig in visualizations])
            ]))
        
        # Recommendations Section with Action Buttons
        recommendations = SummaryAnalyzer.generate_recommendations(df, secondary_ticker)
        if recommendations:
            rec_cards = []
            for rec in recommendations:
                card = dbc.Card([
                    dbc.CardBody([
                        html.H5(rec['title'], style={'color': '#00ff41', 'marginBottom': '10px'}),
                        html.P(rec['description'], style={'fontSize': '1rem', 'marginBottom': '15px'}),
                        dbc.Button(
                            "🚀 Run This Analysis",
                            id={'type': 'follow-up-btn', 'index': rec['id']},
                            color='success',
                            size='sm',
                            className='me-2',
                            n_clicks=0,
                            style={'backgroundColor': '#00ff41', 'border': 'none', 'color': '#000'}
                        ),
                        html.Div(id={'type': 'follow-up-status', 'index': rec['id']}, 
                                style={'marginTop': '10px', 'color': '#aaa', 'fontSize': '0.9rem'})
                    ])
                ], style={'backgroundColor': 'rgba(0, 255, 65, 0.05)', 'border': '1px solid #00ff41', 
                         'marginBottom': '15px', 'boxShadow': '0 0 10px rgba(0, 255, 65, 0.2)'})
                rec_cards.append(card)
            
            summary_content.append(html.Div([
                html.H4("🎯 Recommended Follow-Up Analyses", 
                       style={'color': '#00ff41', 'marginTop': '30px', 'marginBottom': '15px'}),
                html.P("Click any button below to automatically run deeper analysis based on your results:", 
                      style={'color': '#aaa', 'marginBottom': '20px'}),
                html.Div(rec_cards)
            ]))
        
        return html.Div(summary_content, style={'padding': '20px'})
    
    elif active_tab == 'tab-table':
        # Create interactive data table
        return dash_table.DataTable(
            id='results-table',
            columns=[{"name": i, "id": i} for i in df.columns],
            data=df.to_dict('records'),
            sort_action="native",
            filter_action="native",
            page_action="native",
            page_size=10,
            style_cell={
                'backgroundColor': 'rgba(0, 0, 0, 0.6)',
                'color': '#fff',
                'border': '1px solid #444'
            },
            style_header={
                'backgroundColor': 'rgba(0, 255, 65, 0.1)',
                'color': '#00ff41',
                'fontWeight': 'bold'
            },
            style_data_conditional=[
                {
                    'if': {'column_id': 'Sharpe Ratio', 'filter_query': '{Sharpe Ratio} > 1'},
                    'color': '#00ff41',
                    'fontWeight': 'bold'
                },
                {
                    'if': {'column_id': 'Sharpe Ratio', 'filter_query': '{Sharpe Ratio} < 0'},
                    'color': '#ff0040'
                },
                {
                    'if': {'column_id': 'Significant 95%', 'filter_query': '{Significant 95%} = Yes'},
                    'backgroundColor': 'rgba(0, 255, 65, 0.1)'
                }
            ]
        )
    
    elif active_tab == 'tab-charts':
        # Create performance charts
        charts = []
        
        # Sharpe Ratio Distribution
        fig_sharpe = px.histogram(df, x='Sharpe Ratio', nbins=20,
                                  title='Sharpe Ratio Distribution',
                                  color_discrete_sequence=['#00ff41'])
        fig_sharpe.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_sharpe))
        
        # Win Rate vs Total Capture Scatter
        fig_scatter = px.scatter(df, x='Win Ratio (%)', y='Total Capture (%)',
                                 text='Primary Ticker', 
                                 size='Trigger Days',
                                 color='Sharpe Ratio',
                                 color_continuous_scale='Viridis',
                                 title='Win Rate vs Total Capture')
        fig_scatter.update_traces(textposition='top center')
        fig_scatter.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_scatter))
        
        # Top 10 Performers Bar Chart
        top_10 = df.nlargest(10, 'Sharpe Ratio')
        fig_bar = px.bar(top_10, x='Primary Ticker', y='Sharpe Ratio',
                         title='Top 10 Performers by Sharpe Ratio',
                         color='Sharpe Ratio',
                         color_continuous_scale='Viridis')
        fig_bar.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0.1)',
            font={'color': '#00ff41'},
            xaxis={'gridcolor': '#333'},
            yaxis={'gridcolor': '#333'}
        )
        charts.append(dcc.Graph(figure=fig_bar))
        
        return html.Div(charts)
    
    elif active_tab == 'tab-stats':
        # Statistical analysis display
        stats_cards = []
        
        for _, row in df.iterrows():
            # Handle numeric conversion safely
            try:
                sharpe_ratio = float(row['Sharpe Ratio']) if row['Sharpe Ratio'] != 'N/A' else 0.0
                p_value = row['p-Value']
                wins = int(row['Wins']) if pd.notna(row['Wins']) else 0
                losses = int(row['Losses']) if pd.notna(row['Losses']) else 0
            except (ValueError, TypeError) as e:
                logger.error(f"Error converting values for {row.get('Primary Ticker', 'Unknown')}: {e}")
                continue
                
            # Create the Sharpe gauge figure and wrap it in dcc.Graph
            sharpe_fig = VisualMetrics.create_sharpe_gauge(sharpe_ratio)
            
            # Ensure the figure is wrapped in dcc.Graph
            if hasattr(sharpe_fig, 'data') and hasattr(sharpe_fig, 'layout'):
                # This is a Plotly figure object, wrap it
                sharpe_component = dcc.Graph(
                    figure=sharpe_fig, 
                    config={'displayModeBar': False},
                    style={'height': '250px'}
                )
            else:
                # Fallback in case it's already a component
                sharpe_component = sharpe_fig
            
            card = dbc.Card([
                dbc.CardHeader(html.H5(row['Primary Ticker'], style={'color': '#00ff41'})),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            sharpe_component
                        ], width=6),
                        dbc.Col([
                            VisualMetrics.create_significance_meter(p_value),
                            html.Hr(),
                            VisualMetrics.create_win_rate_visual(wins, losses)
                        ], width=6)
                    ])
                ])
            ], style={'backgroundColor': 'rgba(0, 0, 0, 0.6)', 'border': '1px solid #444',
                     'marginBottom': '20px'})
            stats_cards.append(card)
        
        return html.Div(stats_cards)
    
    elif active_tab == 'tab-correlation':
        # Correlation analysis
        return html.Div([
            VisualMetrics.create_correlation_heatmap(df),
            html.Hr(style={'borderColor': '#444', 'margin': '30px 0'}),
            VisualMetrics.create_advanced_scatter_matrix(df)
        ])
    
    elif active_tab == 'tab-advanced':
        # Advanced analytics
        advanced_content = []
        
        # Risk-Return Quadrant Analysis
        if 'Sharpe Ratio' in df.columns and 'Std Dev (%)' in df.columns:
            # Create a copy of df with absolute values for size (Plotly requires non-negative)
            df_plot = df.copy()
            df_plot['Abs Total Capture (%)'] = df['Total Capture (%)'].abs()
            
            fig_quadrant = px.scatter(df_plot, x='Std Dev (%)', y='Sharpe Ratio',
                                     text='Primary Ticker',
                                     size='Abs Total Capture (%)',
                                     color='Win Ratio (%)',
                                     color_continuous_scale='RdYlGn',
                                     title='Risk-Return Quadrant Analysis',
                                     labels={'Std Dev (%)': 'Risk (Std Dev %)',
                                            'Sharpe Ratio': 'Return (Sharpe Ratio)',
                                            'Abs Total Capture (%)': 'Magnitude of Total Capture (%)'})
            
            # Add custom hover data to show actual capture values (including negatives)
            fig_quadrant.update_traces(
                customdata=df[['Total Capture (%)']],
                hovertemplate='<b>%{text}</b><br>' +
                             'Risk (Std Dev): %{x:.2f}%<br>' +
                             'Sharpe Ratio: %{y:.2f}<br>' +
                             'Win Ratio: %{marker.color:.1f}%<br>' +
                             'Total Capture: %{customdata[0]:.2f}%<br>' +
                             '<extra></extra>'
            )
            
            # Add quadrant lines
            fig_quadrant.add_hline(y=df['Sharpe Ratio'].median(), line_dash="dash", 
                                  line_color="#444", annotation_text="Median Sharpe")
            fig_quadrant.add_vline(x=df['Std Dev (%)'].median(), line_dash="dash", 
                                  line_color="#444", annotation_text="Median Risk")
            
            fig_quadrant.update_traces(textposition='top center')
            fig_quadrant.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333'},
                yaxis={'gridcolor': '#333'},
                height=500
            )
            advanced_content.append(dcc.Graph(figure=fig_quadrant))
        
        # Time Series of Cumulative Performance (if we have date data)
        if len(df) > 5:
            # Performance ranking visualization
            df_sorted = df.sort_values('Sharpe Ratio', ascending=True)
            fig_ranking = px.bar(df_sorted, y='Primary Ticker', x='Sharpe Ratio',
                               orientation='h',
                               color='Sharpe Ratio',
                               color_continuous_scale='Viridis',
                               title='Performance Ranking - All Tickers')
            
            fig_ranking.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333'},
                yaxis={'gridcolor': '#333'},
                height=max(400, len(df) * 25)
            )
            advanced_content.append(dcc.Graph(figure=fig_ranking))
        
        # Distribution analysis
        if 'Total Capture (%)' in df.columns:
            fig_dist = go.Figure()
            
            # Add histogram
            fig_dist.add_trace(go.Histogram(
                x=df['Total Capture (%)'],
                name='Distribution',
                marker_color='#00ff41',
                opacity=0.7
            ))
            
            # Add box plot
            fig_dist.add_trace(go.Box(
                x=df['Total Capture (%)'],
                name='Box Plot',
                marker_color='#80ff00',
                y=['Total Capture'] * len(df)
            ))
            
            fig_dist.update_layout(
                title='Total Capture Distribution Analysis',
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0.1)',
                font={'color': '#00ff41'},
                xaxis={'gridcolor': '#333', 'title': 'Total Capture (%)'},
                yaxis={'gridcolor': '#333'},
                showlegend=False,
                height=400
            )
            advanced_content.append(dcc.Graph(figure=fig_dist))
        
        if advanced_content:
            return html.Div(advanced_content)
        else:
            return html.Div("Insufficient data for advanced analytics", 
                          style={'color': '#aaa', 'textAlign': 'center', 'padding': '20px'})
    
    return html.Div("Select a tab to view results.", style={'color': '#aaa'})

# Callback for follow-up analysis buttons
@app.callback(
    [Output({'type': 'follow-up-status', 'index': ALL}, 'children'),
     Output('primary-tickers-input', 'value', allow_duplicate=True),
     Output('secondary-ticker-input', 'value', allow_duplicate=True),
     Output('follow-up-action-store', 'data')],
    [Input({'type': 'follow-up-btn', 'index': ALL}, 'n_clicks')],
    [State('analysis-results-store', 'data'),
     State('secondary-ticker-input', 'value')],
    prevent_initial_call=True
)
def handle_follow_up_analysis(n_clicks_list, results_data, secondary_ticker):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_list):
        raise dash.exceptions.PreventUpdate
    
    # Get which button was clicked
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    button_dict = json.loads(button_id)
    action_id = button_dict['index']
    
    # Create status messages for all buttons
    status_messages = ["" for _ in n_clicks_list]
    
    # Get the index of the clicked button
    for idx, clicks in enumerate(n_clicks_list):
        if clicks and clicks > 0:
            # This button was clicked
            status_messages[idx] = "🔄 Preparing analysis..."
    
    # Generate the recommendations to get the action details
    df = pd.DataFrame(results_data)
    recommendations = SummaryAnalyzer.generate_recommendations(df, secondary_ticker)
    
    # Find the matching recommendation
    action = None
    for rec in recommendations:
        if rec['id'] == action_id:
            action = rec
            break
    
    if not action:
        return status_messages, dash.no_update, dash.no_update, dash.no_update
    
    # Prepare new analysis based on action type
    new_primary_tickers = ""
    new_secondary_ticker = secondary_ticker
    
    if action['action'] == 'deep_dive':
        # Set up for deep dive on top performers
        new_primary_tickers = ', '.join(action['params']['tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Loaded {len(action['params']['tickers'])} top performers for detailed analysis."
    
    elif action['action'] == 'find_similar':
        # Find similar tickers (example implementation)
        reference = action['params']['reference_ticker']
        # In a real implementation, you'd have a similarity function
        similar_tickers = ['AAPL', 'MSFT', 'GOOGL'] if reference != 'AAPL' else ['META', 'NVDA', 'AMD']
        new_primary_tickers = ', '.join(similar_tickers)
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Found {len(similar_tickers)} similar tickers to {reference}."
    
    elif action['action'] == 'outlier_analysis':
        # Set up for outlier analysis
        new_primary_tickers = ', '.join(action['params']['outlier_tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = f"✅ Ready! Loaded {len(action['params']['outlier_tickers'])} outlier tickers for investigation."
    
    elif action['action'] == 'time_analysis':
        # For time analysis, use top tickers
        new_primary_tickers = ', '.join(action['params']['top_tickers'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = "✅ Ready! Loaded top tickers for time period optimization."
    
    elif action['action'] == 'sector_analysis':
        # Load sector ETFs
        new_primary_tickers = ', '.join(action['params']['sectors'])
        status_messages[n_clicks_list.index(max(n_clicks_list))] = "✅ Ready! Loaded sector ETFs for rotation analysis."
    
    # Return the updates
    return status_messages, new_primary_tickers, new_secondary_ticker, action

# Add custom CSS for animations
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(0, 255, 65, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(0, 255, 65, 0); }
                100% { box-shadow: 0 0 0 0 rgba(0, 255, 65, 0); }
            }
            .pulse-animation {
                animation: pulse 2s infinite;
            }
            body {
                background-color: #0a0a0a;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''

if __name__ == "__main__":
    # Optional: log parity status once at boot (no-op if fallback)
    try:
        log_parity_status()
    except Exception:
        pass
    
    # Skip initialization in reloader subprocess (prevent double execution)
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # Ensure all required directories exist
        required_dirs = ['cache', 'cache/impact_analysis', 'cache/results', 'cache/status', 
                        'cache/sma_cache', 'cache/templates', 'output', 'logs']
        for directory in required_dirs:
            os.makedirs(directory, exist_ok=True)
        
        # Clean up old log files
        log_files = ['logs/analysis.log', 'logs/debug.log', 'logs/impactsearch.log']
        for file in log_files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass
                
    # Use debug=False or use_reloader=False to prevent hanging processes
    app.run_server(debug=True, port=8051, use_reloader=False)