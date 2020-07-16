import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')

OUTPUT_DIR = './tests/output'

import functools

from pdfalcon.pdf import PdfFile


# use `qpdfview <file>` to open pdf and view logs


def write_to_file(test):
    @functools.wraps(test)
    def fn(*args, **kwargs):
        pdf = test(*args, **kwargs)

        with open(f'{OUTPUT_DIR}/{test.__name__}.pdf', 'wb') as f:
            pdf.write(f)
    return fn


@write_to_file
def test_write_text():
    pdf = PdfFile().setup()
    page = pdf.add_page()
    text_obj = page.add_text("basic text", size=40, line_size=42, translate_x=150, translate_y=200, skew_angle_a=20, skew_angle_b=30)
    return pdf


def test_read_text():
    with open(f'{OUTPUT_DIR}/{test_write_text.__name__}.pdf', 'rb') as f:
        pdf = PdfFile().read(f)
    assert pdf.version == 1.4

    sec = pdf.sections[0]
    assert len(sec.crt_section.subsections[0].entries) == 6
    assert sec.trailer.crt_byte_offset == 516
    assert sec.trailer.trailer_dict['Root'].object_number == 2
    assert sec.trailer.trailer_dict['Root'].generation_number == 0
    assert sec.trailer.size.value == 6

    # intentionally missing the zeroth object (bc it's free)
    assert len(pdf.object_store) == 5
