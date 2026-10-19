# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ('assets', 'assets'),
]

hiddenimports = [
    'core',
    'core.database',
    'core.scanner',
    'core.indexer',
    'core.ocr',
    'core.embeddings',
    'core.vector_index',
    'core.image_utils',
    'core.extractors',
    'core.extractors.text_extractor',
    'core.extractors.pdf_extractor',
    'core.extractors.office_extractor',
    'core.extractors.spreadsheet_extractor',
    'core.extractors.presentation_extractor',
    'core.extractors.rtf_extractor',
    'core.extractors.psd_extractor',
    'utils',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'pytesseract',
    'PIL',
    'numpy',
    'pymupdf',
    'docx',
    'openpyxl',
    'xlrd',
    'pptx',
    'odf',
    'striprtf',
    'psd_tools',
]

try:
    import faiss
    hiddenimports.append('faiss')
except ImportError:
    pass

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'IPython', 'notebook'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MindVault',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MindVault',
)
