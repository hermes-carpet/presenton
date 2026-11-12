import subprocess
import sys
import textwrap

import pytest

from templates.pptx_font_utils import normalize_font_family_name
from services.chat.tools import ChatTools


def _run_bounded(code: str) -> None:
    # Bound hostile-input probes in a separate process so a regression cannot
    # stall the whole test suite, including on platforms without SIGALRM.
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("XMLHttpRequest", "XML Http Request"),
        ("OpenSans-Bold", "Open Sans"),
        ("Arial Gras", "Arial"),
        ("Roboto Condensed Bold", "Roboto Condensed"),
        ("Arial Narrow", "Arial Narrow"),
        ("", ""),
    ],
)
def test_font_normalization_preserves_family_boundaries(raw, expected):
    assert normalize_font_family_name(raw) == expected


def test_font_normalization_bounds_long_acronyms():
    _run_bounded("""
        from templates.pptx_font_utils import normalize_font_family_name
        for value in ("A" * 100_000, "A" * 100_000 + "a"):
            result = normalize_font_family_name(value)
            assert result.replace(" ", "") == value
    """)


@pytest.mark.parametrize(
    "message",
    [
        "Revenue 12 and Profit -3.5",
        "Revenue: 12; Profit = -3.5",
        "Revenue has 12\nProfit with -3.5",
        "Revenue 12 |\n and Profit -3.5",
        "Revenue 12\u2003AnD\u00a0Profit -3.5",
        "Revenue 12 and and Profit -3.5",
    ],
)
def test_chart_parser_preserves_separators_and_numbers(message):
    assert ChatTools._extract_chart_rows_from_user_message(message) == [
        {"label": "Revenue", "value": 12},
        {"label": "Profit", "value": -3.5},
    ]


def test_chart_parser_bounds_whitespace_runs():
    _run_bounded("""
        from services.chat.tools import ChatTools
        for value in ("a" + " " * 8_000 + "z", "a" + "\\t" * 8_000 + "z"):
            assert ChatTools._extract_chart_rows_from_user_message(value) == []
        value = "Revenue" + " " * 8_000 + "12 and Profit -3.5"
        assert ChatTools._extract_chart_rows_from_user_message(value) == [
            {"label": "Revenue", "value": 12},
            {"label": "Profit", "value": -3.5},
        ]
    """)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Name, Age | Department", ["Name", "Age", "Department"]),
        ("a | | , b", ["a", "b"]),
        ("- Name: , Age . | Department—", ["Name", "Age", "Department"]),
        ("\u2003outer\u2003, \u2003inner\u00a0|tail\u2003", ["\u2003outer", "inner", "tail\u2003"]),
        ("", []),
    ],
)
def test_table_cells_preserve_delimiters_and_trimming(text, expected):
    assert ChatTools._split_table_cells(text) == expected


def test_table_cells_bound_long_whitespace_runs():
    _run_bounded("""
        from services.chat.tools import ChatTools
        for whitespace in (" ", "\\u2003"):
            value = "a" + whitespace * 100_000 + "z"
            assert ChatTools._split_table_cells(value) == [value]
    """)


@pytest.mark.parametrize("prefix", ["Headers are", "headers with", "Columns:"])
def test_table_parser_preserves_header_markers(prefix):
    assert ChatTools._extract_table_from_user_message(
        f"{prefix} Name, Age. Data: Ada, 36; Grace, 44"
    ) == {"columns": ["Name", "Age"], "rows": [["Ada", "36"], ["Grace", "44"]]}


def test_table_headers_bound_rejected_whitespace():
    _run_bounded("""
        from services.chat.tools import ChatTools
        for marker in ("headers", "columns"):
            value = marker + "\\n" * 100_000 + ". Data: a,b;c,d"
            assert ChatTools._extract_table_from_user_message(value) is None
    """)


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "\u00a0", "\u2003"])
def test_table_data_marker_preserves_unicode_and_multiline_payloads(whitespace):
    assert ChatTools._extract_table_from_user_message(
        f"Headers Name, Age. dAtA{whitespace}:{whitespace}Ada, 36; Grace, 44"
    ) == {"columns": ["Name", "Age"], "rows": [["Ada", "36"], ["Grace", "44"]]}


def test_table_data_marker_preserves_context_whitespace_rows_and_empty_fallback():
    assert ChatTools._extract_table_from_user_message(
        "UI context: control\nUser message: Headers Name. Data:\u00a0"
    ) == {"columns": ["Name"], "rows": [["\u00a0"]]}
    assert ChatTools._extract_table_from_user_message("Headers Name. Data:") is None


def test_table_data_marker_bounds_failed_markers_and_whitespace_payloads():
    _run_bounded("""
        from services.chat.tools import ChatTools
        for value in ('data ' * 100000, 'data' + '\\u2003' * 100000,
                      'Headers Name. Data:' + ' ' * 100000):
            assert ChatTools._extract_table_from_user_message(value) is None
    """)
