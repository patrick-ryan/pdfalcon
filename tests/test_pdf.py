# TODO: for file setup/reading, maybe find better way to serialize the objects,
#   so that it's just a deep comparison of dicts;
#   add more parser (and maybe formatter) unit tests

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../')

OUTPUT_DIR = './tests/output'
if not os.path.exists(OUTPUT_DIR):
    os.mkdir(OUTPUT_DIR)

import io
import functools
import pytest
import textwrap

from pdfalcon.pdf import PdfFile, DocumentCatalog, PageTreeNode, PageObject, ContentStream
from pdfalcon.types import parse_pdf_object, \
    PdfArray, PdfDict, PdfIndirectObject, PdfInteger, PdfLiteralString, PdfName, PdfReal, PdfStream, \
    ConcatenateMatrixOperation, StateRestoreOperation, StateSaveOperation, StreamTextObject, \
    TextFontOperation, TextLeadingOperation, TextMatrixOperation, TextNextLineOperation, TextShowOperation
from pdfalcon.utils import get_inherited_entry, get_optional_entry, read_lines, read_pdf_tokens, reverse_read_lines


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


@pytest.mark.dependency()
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
    assert len(pdf.document_catalog.page_tree.pdf_object.contents['Kids']) == 0
    assert pdf.document_catalog.page_tree.pdf_object.contents['Count'] == 0
    assert len(pdf.document_catalog.page_tree.children) == 0
    assert sec.body.zeroth_object.object_key == (0, 65535)
    assert sec.body.free_object_list_tail is not None
    assert len(sec.crt_section.subsections[0].entries) == 3
    assert sec.trailer.size.value == 3
    assert len(sec.body.objects) == 3

    page = pdf.add_page()
    assert isinstance(page, PageObject)
    assert len(pdf.document_catalog.page_tree.pdf_object.contents['Kids']) == 1
    assert pdf.document_catalog.page_tree.pdf_object.contents['Count'] == 1
    assert len(pdf.document_catalog.page_tree.children) == 1
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


@pytest.mark.dependency(depends=["test_write_text"])
@read_from_file(test_write_text)
def test_read_text(pdf=None):
    assert pdf.version == 1.4
    sec = pdf.sections[0]
    assert len(sec.crt_section.subsections[0].entries) == 6
    assert sec.trailer.crt_byte_offset is not None
    assert sec.trailer.trailer_dict['Root'].object_number == 2
    assert sec.trailer.trailer_dict['Root'].generation_number == 0
    assert sec.trailer.size.value == 6
    # intentionally missing the zeroth object (bc it's free)
    assert len(pdf.object_store) == 5
    assert isinstance(pdf.document_catalog, DocumentCatalog)
    assert isinstance(pdf.document_catalog.page_tree, PageTreeNode)
    assert len(pdf.document_catalog.page_tree.pdf_object.contents['Kids']) == 1
    assert pdf.document_catalog.page_tree.pdf_object.contents['Count'] == 1
    assert len(pdf.document_catalog.page_tree.children) == 1
    assert sec.body.zeroth_object.object_key == (0, 65535)
    assert sec.body.free_object_list_tail is not None
    assert isinstance(pdf.document_catalog.page_tree.children[0], PageObject)
    assert isinstance(pdf.document_catalog.page_tree.children[0].objects[0], ContentStream)
    assert len(pdf.document_catalog.page_tree.children[0].objects[0].contents) == 4
    assert isinstance(pdf.document_catalog.page_tree.children[0].objects[0].contents[2], StreamTextObject)
    assert len(pdf.document_catalog.page_tree.children[0].objects[0].contents[2].contents) == 5
    assert len(sec.body.objects) == 6


def test_parse_indirect_object():
    io_buffer = io.BytesIO(
        textwrap.dedent('''
            1 0 obj
              42
            endobj
        ''').strip().encode('utf-8')
    )
    pdf_object = PdfIndirectObject().parse(io_buffer)
    assert isinstance(pdf_object.contents, PdfInteger)
    assert pdf_object.contents.value == 42


def test_parse_dict():
    io_buffer = io.BytesIO(
        textwrap.dedent('''
            <<
              /Test 42
              /Foo /Bar
            >>
        ''').strip().encode('utf-8')
    )
    dict_ = parse_pdf_object(io_buffer)
    assert isinstance(dict_, PdfDict)
    assert dict_ == {'Test': 42, 'Foo': 'Bar'}


def test_parse_stream():
    io_buffer = io.BytesIO(
        textwrap.dedent('''
            <<
              /Length 3
            >>
            stream
              q
            endstream
        ''').strip().encode('utf-8')
    )
    stream = parse_pdf_object(io_buffer)
    assert isinstance(stream, PdfStream)
    assert stream == PdfStream(stream_dict={'Length': 3}, contents=[StateSaveOperation()])


def test_parse_literal_string():
    io_buffer = io.BytesIO(
        textwrap.dedent('''
            (test literal string)
        ''').strip().encode('utf-8')
    )
    str_ = parse_pdf_object(io_buffer)
    assert isinstance(str_, PdfLiteralString)
    assert str_ == 'test literal string'


@pytest.mark.dependency(depends=["test_write_text"])
@read_from_file(test_write_text)
@write_to_file
def test_clone(pdf=None):
    new_pdf = pdf.clone()

    assert new_pdf is not pdf

    sec = new_pdf.sections[0]
    assert isinstance(new_pdf.document_catalog, DocumentCatalog)
    assert isinstance(new_pdf.document_catalog.page_tree, PageTreeNode)
    assert len(new_pdf.document_catalog.page_tree.pdf_object.contents['Kids']) == 1
    assert new_pdf.document_catalog.page_tree.pdf_object.contents['Count'] == 1
    assert len(new_pdf.document_catalog.page_tree.children) == 1
    assert sec.body.zeroth_object.object_key == (0, 65535)
    assert sec.body.free_object_list_tail is not None
    assert len(sec.crt_section.subsections[0].entries) == 3
    assert sec.trailer.size.value == 3
    assert len(sec.body.objects) == 3

    sec2 = new_pdf.sections[1]
    assert isinstance(new_pdf.document_catalog.page_tree.children[0].objects[0], ContentStream)
    assert len(new_pdf.document_catalog.page_tree.children[0].objects[0].contents) == 4
    assert len(sec2.crt_section.subsections[0].entries) == 2
    assert sec2.trailer.size.value == 5
    assert len(sec2.body.objects) == 5

    assert (set(sec.body.objects) & set(sec2.body.objects)) == {(0,65535), (1,0)}

    return new_pdf
