# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for Investment Tracker.
# Run build.bat to produce the distributable in dist\InvestmentTracker\.

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('icon.ico', '.'),
    ],
    hiddenimports=[
        # Flask internals
        'flask',
        'jinja2',
        'werkzeug',
        # Data libraries
        'pandas',
        'openpyxl',
        'openpyxl.styles',
        # yfinance and its dependencies
        'yfinance',
        'requests',
        'urllib3',
        # PyWebView Windows backend
        'webview',
        'webview.platforms.winforms',
        'clr',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='InvestmentTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # no black terminal window behind the app
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='InvestmentTracker',
)
