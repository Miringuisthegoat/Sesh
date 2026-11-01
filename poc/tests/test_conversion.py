import pytest
from pathlib import Path
from poc import make_corpus
from poc.convert import convert
from poc.errors import ConversionError
from poc.validate import validate_pdf

CORPUS = make_corpus.OUT


@pytest.fixture(scope="session", autouse=True)
def corpus():
    make_corpus.main()


GOOD = ["docx_simple.docx", "docx_rich_multipage.docx", "docx_landscape_letter.docx", "xlsx_simple.xlsx",
        "xlsx_multisheet_printarea.xlsx", "xlsx_wide_30cols.xlsx", "image_photo.jpg", "image_portrait.png"]


@pytest.mark.parametrize("name", GOOD)
def test_good_files_make_valid_pdfs(name, tmp_path):
    out = tmp_path / "o.pdf"
    convert(CORPUS / name, out)
    v = validate_pdf(out)
    assert v["ok"] and v["pages"] >= 1 and len(v["sha256"]) == 64


def test_multipage_docx_has_multiple_pages(tmp_path):
    convert(CORPUS / "docx_rich_multipage.docx", tmp_path / "o.pdf")
    assert validate_pdf(tmp_path / "o.pdf")["pages"] >= 3


def test_landscape_page_size_honoured(tmp_path):
    convert(CORPUS / "docx_landscape_letter.docx", tmp_path / "o.pdf")
    assert validate_pdf(tmp_path / "o.pdf")["page_sizes"][0] == (792, 612)


def test_text_is_extractable(tmp_path):
    convert(CORPUS / "docx_simple.docx", tmp_path / "o.pdf")
    assert validate_pdf(tmp_path / "o.pdf")["text_chars"] > 50


@pytest.mark.parametrize("name,code", [
    ("bad_malformed.docx", "SIGNATURE_MISMATCH"), ("bad_fake_zip.docx", "DOCX_MALFORMED"),
    ("bad_encrypted_like.xlsx", "XLSX_ENCRYPTED_OR_LEGACY"), ("bad_png_is_text.png", "SIGNATURE_MISMATCH"),
    ("bad_image_huge_dimensions.png", "IMAGE_TOO_LARGE"), ("bad_unsupported.pptx", "UNSUPPORTED_TYPE"),
])
def test_bad_files_rejected_with_code(name, code, tmp_path):
    with pytest.raises(ConversionError) as e:
        convert(CORPUS / name, tmp_path / "o.pdf")
    assert e.value.code == code and not e.value.retryable
    assert not (tmp_path / "o.pdf").exists()


def test_uncached_formula_warns(tmp_path):
    w = convert(CORPUS / "xlsx_formulas_uncached.xlsx", tmp_path / "o.pdf")
    assert any("cached" in x for x in w)
