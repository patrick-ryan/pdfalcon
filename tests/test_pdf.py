import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')


from pdfalcon.pdf import PdfFile


# use `qpdfview <file>` to open pdf and view logs


def test_build_pdf_file():
    pdf = PdfFile()
    page = pdf.add_page()
    text_obj = page.add_text("basic test", translate_x=75, translate_y=100, scale_x=5, scale_y=5, skew_angle_x=5, skew_angle_y=5)
    # print()
    # print(pdf.format())
    # print()

    io_buffer = io.BytesIO()
    pdf.write(io_buffer)
    with open('./output/first-text.pdf', 'wb') as f:
        f.write(io_buffer.getbuffer())

    # assert pdf.to_pretty_dict() == {'body': {'document_catalog': {'pages': [{'page': {'text': 'basic test'}}]}}}

