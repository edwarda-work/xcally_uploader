from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH).parent
assets = ["index.html", "agents.js", "campaigns.js", "performance.js", "campaign-controls.js", "qa.js"]
a = Analysis(
    [str(root / "desktop.py")], pathex=[str(root)],
    # soundfile decodes xCALLY's GSM recordings; it ships libsndfile in _soundfile_data.
    binaries=collect_dynamic_libs("_soundfile_data"), datas=[(str(root / name), ".") for name in assets],
    hiddenimports=["soundfile"], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="XcallyUploads", debug=False, strip=False, upx=False, console=False,
)
