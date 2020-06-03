
from pdfalcon import PdfFile


def test_build_pdf_file():
    pdf = PdfFile.build()
    page = pdf.add_page()
    content = page.add_text("basic test")

    assert pdf.to_pretty_dict() == {'body': {'document_catalog': {'pages': [{'page': {'text': 'basic test'}}]}}}

