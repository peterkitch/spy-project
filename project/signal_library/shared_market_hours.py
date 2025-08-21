#!/usr/bin/env python3
"""
Shared market hours and exchange close times.
Single source of truth for both onepass.py and impactsearch.py.
"""

import logging

logger = logging.getLogger(__name__)

def get_exchange_close_time(ticker):
    """
    Get exchange-specific market close time based on ticker suffix.
    
    Args:
        ticker: Stock ticker symbol (e.g., '005930.KS', 'AAPL', 'MC.PA')
    
    Returns:
        tuple: (close_hour, close_minute, timezone_string)
    """
    if not ticker:
        return (16, 0, 'America/New_York')  # Default to US market
    
    ticker_upper = ticker.upper()
    
    # Asian markets
    if ticker_upper.endswith(('.KS', '.KQ')):
        return (15, 30, 'Asia/Seoul')  # Korean markets close at 3:30 PM KST
    elif ticker_upper.endswith('.T'):
        return (15, 0, 'Asia/Tokyo')  # Tokyo Stock Exchange closes at 3:00 PM JST
    elif ticker_upper.endswith('.HK'):
        return (16, 0, 'Asia/Hong_Kong')  # Hong Kong closes at 4:00 PM HKT
    elif ticker_upper.endswith(('.SS', '.SZ')):
        return (15, 0, 'Asia/Shanghai')  # Shanghai/Shenzhen close at 3:00 PM CST
    elif ticker_upper.endswith('.TW'):
        return (13, 30, 'Asia/Taipei')  # Taiwan closes at 1:30 PM CST
    elif ticker_upper.endswith('.SI'):
        return (17, 0, 'Asia/Singapore')  # Singapore closes at 5:00 PM SGT
    elif ticker_upper.endswith('.KL'):
        return (17, 0, 'Asia/Kuala_Lumpur')  # Malaysia closes at 5:00 PM MYT
    elif ticker_upper.endswith(('.NS', '.BO')):
        return (15, 30, 'Asia/Kolkata')  # India markets close at 3:30 PM IST
    elif ticker_upper.endswith('.JK'):
        return (16, 0, 'Asia/Jakarta')  # Indonesia closes at 4:00 PM WIB
    elif ticker_upper.endswith('.BK'):
        return (16, 30, 'Asia/Bangkok')  # Thailand closes at 4:30 PM ICT
    
    # European markets
    elif ticker_upper.endswith('.L'):
        return (16, 30, 'Europe/London')  # London closes at 4:30 PM GMT/BST
    elif ticker_upper.endswith('.PA'):
        return (17, 30, 'Europe/Paris')  # Paris closes at 5:30 PM CET/CEST
    elif ticker_upper.endswith('.DE'):
        return (17, 30, 'Europe/Berlin')  # Frankfurt closes at 5:30 PM CET/CEST
    elif ticker_upper.endswith('.SW'):
        return (17, 30, 'Europe/Zurich')  # Swiss Exchange closes at 5:30 PM CET/CEST
    elif ticker_upper.endswith('.AS'):
        return (17, 30, 'Europe/Amsterdam')  # Amsterdam closes at 5:30 PM CET/CEST
    elif ticker_upper.endswith('.MI'):
        return (17, 30, 'Europe/Rome')  # Milan closes at 5:30 PM CET/CEST
    elif ticker_upper.endswith('.MC'):
        return (17, 30, 'Europe/Madrid')  # Madrid closes at 5:30 PM CET/CEST
    
    # Americas (non-US)
    elif ticker_upper.endswith(('.TO', '.V')):
        return (16, 0, 'America/Toronto')  # Toronto closes at 4:00 PM ET
    elif ticker_upper.endswith('.SA'):
        return (18, 0, 'America/Sao_Paulo')  # São Paulo closes at 6:00 PM BRT
    elif ticker_upper.endswith('.MX'):
        return (15, 0, 'America/Mexico_City')  # Mexico closes at 3:00 PM CST
    
    # Oceania
    elif ticker_upper.endswith('.AX'):
        return (16, 0, 'Australia/Sydney')  # Australia closes at 4:00 PM AEDT/AEST
    elif ticker_upper.endswith('.NZ'):
        return (17, 0, 'Pacific/Auckland')  # New Zealand closes at 5:00 PM NZDT/NZST
    
    # Other
    elif ticker_upper.endswith('.JO'):
        return (17, 0, 'Africa/Johannesburg')  # Johannesburg closes at 5:00 PM SAST
    
    # Default to US market hours
    return (16, 0, 'America/New_York')  # 4:00 PM ET

# Define comprehensive Asian suffix list for conservative handling
ASIA_SUFFIXES = (
    '.KS', '.KQ',  # Korea
    '.T',          # Japan
    '.HK',         # Hong Kong
    '.SS', '.SZ',  # China
    '.TW',         # Taiwan
    '.SI',         # Singapore
    '.KL',         # Malaysia
    '.NS', '.BO',  # India
    '.JK',         # Indonesia
    '.BK'          # Thailand
)

def is_asian_market(ticker):
    """
    Check if a ticker belongs to an Asian market.
    
    Args:
        ticker: Stock ticker symbol
    
    Returns:
        bool: True if Asian market, False otherwise
    """
    if not ticker:
        return False
    return ticker.upper().endswith(ASIA_SUFFIXES)