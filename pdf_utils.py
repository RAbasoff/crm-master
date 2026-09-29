"""fpdf2 bootstrap and font discovery."""
import os


def find_pdf_font():
    """Locate a Unicode TTF for fpdf2. Prefers bundled DejaVu, then system fonts."""
    root = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(root, 'static', 'fonts', 'DejaVuSans.ttf'),
        os.path.join(root, 'static', 'fonts', 'arial.ttf'),
        r'C:\Windows\Fonts\dejavu\DejaVuSans.ttf',
        r'C:\Windows\Fonts\arial.ttf',
        r'C:\Windows\Fonts\calibri.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        '/home/rabasoff/.local/share/fonts/DejaVuSans.ttf',
    ):
        if os.path.exists(cand):
            return cand
    return None


def ensure_fpdf():
    """Import fpdf2. Order: system package → pip install → vendor_fpdf (Helvetica-only)."""
    import sys
    import subprocess

    def _try_import():
        try:
            import fpdf
            # Prefer the real library, not our vendor copy, when both exist
            return fpdf
        except ImportError:
            return None

    # already imported?
    mod = _try_import()
    if mod is not None:
        return mod

    root = os.path.dirname(os.path.abspath(__file__))
    vendor = os.path.join(root, '.vendor')

    # 1) install into current interpreter (venv: `pip install fpdf2` — NOT --user)
    # 2) install into project .vendor
    last_err = ''
    for args in (
        [sys.executable, '-m', 'pip', 'install', '--no-cache-dir', 'fpdf2'],
        [sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--target', vendor, 'fpdf2'],
    ):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                last_err = f'{" ".join(args)} rc={p.returncode}: {(p.stderr or p.stdout or "")[-400:]}'
                print(f'ensure_fpdf: {last_err}')
                continue
            if '--target' in args and vendor not in sys.path:
                sys.path.insert(0, vendor)
            for m in list(sys.modules):
                if m == 'fpdf' or m.startswith('fpdf.'):
                    del sys.modules[m]
            mod = _try_import()
            if mod is not None:
                print(f'ensure_fpdf: installed fpdf2 via {" ".join(args)}')
                return mod
        except Exception as e:
            last_err = f'{" ".join(args)}: {e}'
            print(f'ensure_fpdf: {last_err}')

    # 3) bundled copy in vendor_fpdf/ (always present in the repo)
    vendor_fp = os.path.join(root, 'vendor_fpdf')
    if os.path.isdir(vendor_fp) and vendor_fp not in sys.path:
        sys.path.insert(0, vendor_fp)
    for m in list(sys.modules):
        if m == 'fpdf' or m.startswith('fpdf.'):
            del sys.modules[m]
    mod = _try_import()
    if mod is not None:
        print(f'ensure_fpdf: using bundled vendor_fpdf ({mod.__file__})')
        return mod

    raise ImportError(f'fpdf2 is not installed and auto-install failed. {last_err}. '
                      f'On PA console run: pip install fpdf2')


