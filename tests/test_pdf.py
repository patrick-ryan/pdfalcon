import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')


from pdfalcon.pdf import PdfFile


# use `qpdfview <file>` to open pdf and view logs


def test_build_pdf_file():
    pdf = PdfFile()
    page = pdf.add_page()
    text_obj = page.add_text("basic text", size=40, line_size=42, translate_x=150, translate_y=200, skew_angle_a=20, skew_angle_b=30)
    # print()
    # print(pdf.format())
    # print()

    io_buffer = io.BytesIO()
    pdf.write(io_buffer)
    with open('./output/text.pdf', 'wb') as f:
        f.write(io_buffer.getbuffer())

    # assert pdf.to_pretty_dict() == {'body': {'document_catalog': {'pages': [{'page': {'text': 'basic test'}}]}}}

