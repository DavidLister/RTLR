# Tests for run naming and port listing in the main program.
# Run with: pytest tests/ -v

import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
import common
import rtlr


def test_sanitize_run_name():
    assert rtlr.sanitize_run_name("S404") == "S404"
    assert rtlr.sanitize_run_name("  S404 HT GaN  ") == "S404_HT_GaN"
    assert rtlr.sanitize_run_name('S404 <bad>:"/\\|?*') == "S404_bad"
    assert rtlr.sanitize_run_name("...") == ""          # Windows rejects dot-only names
    assert rtlr.sanitize_run_name("   ") == ""
    assert len(rtlr.sanitize_run_name("x" * 200)) == rtlr.RUN_NAME_MAX_LEN


def test_run_paths_include_name_in_dir_and_file():
    now = datetime.datetime(2026, 11, 13, 12, 30, 5)
    run_name, run_dir, refl = rtlr.make_run_paths("S404", now)
    assert run_name == "2026-11-13_12-30-05---S404"
    assert run_dir == os.path.join(common.DATA_SUBPATH, run_name)
    assert refl == os.path.join(run_dir, "2026-11-13_12-30-05---S404_Reflectance.csv")


def test_available_ports_returns_device_label_pairs():
    for device, label in rtlr.available_ports():
        assert label.startswith(device)


def test_loader_finds_named_reflectance_file(tmp_path):
    (tmp_path / "2026-11-13_12-30-05---S404_Reflectance.csv").write_text(
        "Time (s),Reflectance (V)\n0,0.1\n5,0.2\n")
    run = analysis.load_run(str(tmp_path))
    assert len(run.t) == 2
