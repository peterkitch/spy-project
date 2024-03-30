@echo off
REM Activate the base Miniconda environment
call "C:\Users\sport\AppData\Local\NVIDIA\MiniConda\Scripts\activate.bat" "C:\Users\sport\AppData\Local\NVIDIA\MiniConda"
REM Now, activate the spyproject2 environment
call conda activate spyproject2
REM Change the directory to your project folder
cd /d "E:\Conda Projects\spy-project\project"
REM Keep the command prompt open for interaction
cmd /k
