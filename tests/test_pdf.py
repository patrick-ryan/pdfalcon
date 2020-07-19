import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')

OUTPUT_DIR = './tests/output'

import functools

from pdfalcon.pdf import PdfFile, DocumentCatalog, PageTreeNode, PageObject, ContentStream, StreamTextObject, parse_pdf_object


# use `qpdfview <file>` to open pdf and view logs


def write_to_file(test):
    @functools.wraps(test)
    def fn(*args, **kwargs):
        pdf = test(*args, **kwargs)

        with open(f'{OUTPUT_DIR}/{test.__name__}.pdf', 'wb') as f:
            pdf.write(f)
    return fn


def read_from_file(write_fn):
    def param_fn(test):
        @functools.wraps(test)
        def fn(*args, **kwargs):
            with open(f'{OUTPUT_DIR}/{write_fn.__name__}.pdf', 'rb') as f:
                pdf = PdfFile().read(f)
            test(*args, pdf=pdf, **kwargs)
        return fn
    return param_fn


@write_to_file
def test_write_text():
    pdf = PdfFile()
    assert pdf.document_catalog is None
    assert len(pdf.sections) == 0
    assert len(pdf.object_store) == 0

    pdf = pdf.setup()
    sec = pdf.sections[0]
    assert isinstance(pdf.document_catalog, DocumentCatalog)
    assert isinstance(pdf.document_catalog.page_tree, PageTreeNode)
    assert len(pdf.document_catalog.page_tree.kids) == 0
    assert pdf.document_catalog.page_tree.count.value == 0
    assert len(pdf.document_catalog.page_tree.children) == 0
    assert sec.body.zeroth_object.object_key == (0, 65535)
    assert sec.body.free_object_list_tail is not None
    assert len(sec.crt_section.subsections[0].entries) == 3
    assert sec.trailer.size.value == 3
    assert len(sec.body.objects) == 3

    page = pdf.add_page()
    assert isinstance(page, PageObject)
    assert len(pdf.document_catalog.page_tree.kids) == 1
    assert pdf.document_catalog.page_tree.count.value == 1
    assert pdf.document_catalog.page_tree.children[0] == page
    assert len(sec.crt_section.subsections[0].entries) == 4
    assert sec.trailer.size.value == 4
    assert len(sec.body.objects) == 4

    text_obj = page.add_text("basic text", size=40, line_size=42, translate_x=150, translate_y=200, skew_angle_a=20, skew_angle_b=30)
    assert isinstance(text_obj, StreamTextObject)
    assert isinstance(pdf.document_catalog.page_tree.children[0].objects[0], ContentStream)
    assert len(pdf.document_catalog.page_tree.children[0].objects[0].contents) == 4
    assert pdf.document_catalog.page_tree.children[0].objects[0].contents[2] == text_obj
    assert len(text_obj.contents) == 5
    assert len(sec.crt_section.subsections[0].entries) == 6
    assert sec.trailer.size.value == 6
    assert len(sec.body.objects) == 6
    return pdf


@read_from_file(test_write_text)
def test_read_text(pdf=None):
    assert pdf.version == 1.4

    sec = pdf.sections[0]
    assert len(sec.crt_section.subsections[0].entries) == 6
    assert sec.trailer.crt_byte_offset == 516
    assert sec.trailer.trailer_dict['Root'].object_number == 2
    assert sec.trailer.trailer_dict['Root'].generation_number == 0
    assert sec.trailer.size.value == 6
    # intentionally missing the zeroth object (bc it's free)
    assert len(pdf.object_store) == 5

