import io
import textwrap

from pdfalcon.exceptions import PdfIoError, PdfBuildError, PdfFormatError, PdfParseError
from pdfalcon.types import PdfArray, PdfDict, PdfIndirectObject, PdfInteger, PdfName, PdfReal, PdfStream, \
    ConcatenateMatrixOperation, StateRestoreOperation, StateSaveOperation, StreamTextObject, \
    TextFontOperation, TextLeadingOperation, TextMatrixOperation, TextNextLineOperation, TextShowOperation
from pdfalcon.utils import get_inherited_entry, get_optional_entry, read_lines, read_pdf_tokens, reverse_read_lines


class PdfFile:
    """
    The idea of the PdfFile is two-fold:
     1. provide an abstract representation of a pdf file as defined by the PDF spec
        (therefore effectively representing an abstract syntax tree)
     2. provide an interface for manipulating pdf files
    """

    def __init__(self, version=None):
        version, _ = get_optional_entry('version', version)
        self.version = PdfReal(version)

        # pdf file structure
        self.header = None
        # ordered list of original section and update sections in the file;
        #   original is at index 0, most recent update is at index -1
        self.sections = []

        # pdf document hierarchy
        self.document_catalog = None
        self.fonts = {}

        # set at format-time
        self.cur_format_byte_offset = None

        # set at parse-time
        self.object_store = {}

    def setup(self):
        # builds a basic structure
        self.header = FileHeader(self)
        section = FileSection(self).setup()
        self.sections = [section]

        self.document_catalog = DocumentCatalog(self).setup()
        return self

    def format(self):
        # convert the pdf file object to pdf syntax
        if len(self.sections) == 0:
            raise PdfBuildError

        output_lines = []
        formatted_header = self.header.format()
        self.cur_format_byte_offset = len(formatted_header.encode())+2
        output_lines.append(formatted_header)

        for section in self.sections:
            formatted_section = section.format()
            self.cur_format_byte_offset += 2
            output_lines.append(formatted_section)
        self.cur_format_byte_offset -= 2

        return '\n\n'.join(output_lines)

    def parse(self, io_buffer):
        # create pdf file and document structures from pdf syntax
        self.header = FileHeader(self)
        self.header.parse(io_buffer)

        while True:
            if len(self.sections) > 0 and self.sections[0].trailer.trailer_dict.get('Prev') is None:
                break
            file_section = FileSection(self).parse(io_buffer)
            self.sections = [file_section] + self.sections

        root = self.sections[-1].trailer.trailer_dict['Root']
        object_key = (root.object_number, root.generation_number)
        if object_key not in self.object_store:
            raise PdfParseError
        pdf_object = self.object_store[object_key]
        self.document_catalog = DocumentCatalog(self).from_object(pdf_object)
        return self

    def add_update(self):
        if len(self.sections) == 0:
            raise PdfBuildError
        file_update = FileSection(self)
        self.sections.append(file_update)
        return file_update

    def add_pdf_object(self, contents):
        return self.sections[-1].add_pdf_object(contents)

    def add_page(self):
        if len(self.sections) == 0:
            raise PdfBuildError
        page = self.document_catalog.page_tree.add_page()
        return page

    def write(self, io_buffer, linearized=False):
        # write pdf file encoded string to the supplied io buffer
        # 
        # TODO: encoding must be handled specially based on the objects being used in PDF;
        #   the encoding will also determine how the cross-reference table and trailer get built;
        #   might want to make encoding/compressing/other-filtering a utility;
        #   possibly make linearized the default to optimize web read performance
        if not isinstance(io_buffer, io.IOBase):
            raise PdfIoError
        if not io_buffer.writable():
            raise PdfIoError
        io_buffer.write(self.format().encode('utf-8'))

    def read(self, io_buffer):
        # validate and parse an io buffer
        if self.document_catalog is not None:
            # already built
            raise PdfBuildError
        if not isinstance(io_buffer, io.IOBase):
            raise PdfIoError
        if not io_buffer.seekable():
            raise PdfIoError
        return self.parse(io_buffer)


class FileHeader:

    _str = textwrap.dedent('''
        %PDF-{version}
        %âãÏÓ
    ''').strip()
    _tokens = _str.encode('utf-8').split()

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

    def format(self):
        return self._str.format(version=self.pdf_file.version.format())

    def parse(self, io_buffer):
        _pdf_version, _ = self._tokens
        _pdf, _ = _pdf_version.split(b'-')
        io_buffer.seek(0, io.SEEK_SET)
        lines = read_lines(io_buffer)
        first_line = next(lines, None)
        if first_line is None:
            raise PdfParseError
        if first_line.startswith(_pdf) is False:
            raise PdfParseError
        self.pdf_file.version = float(first_line[len(_pdf)+1:])
        return self


class FileSection:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

        self.body = None
        self.crt_section = None
        self.trailer = None

    def setup(self):
        self.body = FileBody(self)
        self.crt_section = CrtSection(self)
        self.trailer = FileTrailer(self)

        self.body.setup()
        self.crt_section.setup()
        return self

    def format(self):
        formatted_body = self.body.format()
        self.pdf_file.cur_format_byte_offset += len(formatted_body)+2
        self.trailer.crt_byte_offset = self.pdf_file.cur_format_byte_offset

        formatted_crt_section = self.crt_section.format()
        self.pdf_file.cur_format_byte_offset += len(formatted_crt_section)+2

        formatted_trailer = self.trailer.format()
        self.pdf_file.cur_format_byte_offset += len(formatted_trailer)

        return '\n\n'.join([formatted_body, formatted_crt_section, formatted_trailer])

    def parse(self, io_buffer):
        self.body = FileBody(self)
        self.crt_section = CrtSection(self)
        self.trailer = FileTrailer(self)

        if len(self.pdf_file.sections) == 0:
            # start at end of file to find first trailer
            io_buffer.seek(0, io.SEEK_END)
            lines = reverse_read_lines(io_buffer)
            trailer_start = b'trailer'
            while True:
                next_line = next(lines, None)
                if next_line is None:
                    raise PdfParseError
                if next_line == trailer_start:
                    next(lines, None)  # advances buffer cursor
                    break
            self.trailer.parse(io_buffer)
            io_buffer.seek(self.trailer.crt_byte_offset, io.SEEK_SET)
            self.crt_section.parse(io_buffer)
            self.body.parse(io_buffer)
        else:
            # start where the last update says to start
            prev_crt_byte_offset = self.pdf_file.sections[0].trailer.trailer_dict['Prev']
            io_buffer.seek(prev_crt_byte_offset, io.SEEK_SET)
            self.crt_section.parse(io_buffer)
            self.trailer.parse(io_buffer)
            self.body.parse(io_buffer)

        return self

    def add_pdf_object(self, contents):
        pdf_object = self.body.add_pdf_object(contents)
        entry = self.crt_section.add_pdf_object(pdf_object)
        return pdf_object, entry


class FileBody:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.objects = {}

        self.zeroth_object = None
        self.free_object_list_tail = None

        # set at format-time
        self.object_byte_offset_map = None

    def setup(self):
        # start with zeroth object
        object_number, generation_number = 0, 65535
        pdf_object = self.make_free_object(object_number, generation_number)
        return self

    def make_free_object(self, object_number, generation_number):
        pdf_object = PdfIndirectObject()
        if self.zeroth_object is None:
            if (object_number, generation_number) != (0, 65535):
                raise PdfBuildError
            self.zeroth_object = pdf_object
            self.free_object_list_tail = pdf_object
        pdf_object.attach(object_number, generation_number, None)
        self.objects[(object_number, generation_number)] = pdf_object
        self.release_pdf_object(pdf_object)
        return pdf_object

    def add_pdf_object(self, contents):
        pdf_object = PdfIndirectObject()
        max_object_number, _ = sorted(self.objects)[-1]
        object_number = max_object_number + 1
        generation_number = 0
        pdf_object.attach(object_number, generation_number, contents)
        self.objects[(object_number, generation_number)] = pdf_object
        return pdf_object

    def release_pdf_object(self, pdf_object):
        # produces a free object
        pdf_object.release(self.zeroth_object)
        # set previous tail's next free object
        self.free_object_list_tail.next_free_object = pdf_object
        # set new tail
        self.free_object_list_tail = pdf_object
        return pdf_object

    def format(self):
        byte_offset = self.pdf_section.pdf_file.cur_format_byte_offset
        output_lines = []
        object_byte_offset_map = {}
        for k in sorted(self.objects):
            pdf_object = self.objects[k]
            if pdf_object.attached is True and pdf_object.object_number != 0:
                formatted_object = pdf_object.format()
                object_byte_offset_map[pdf_object.object_key] = byte_offset
                byte_offset += len(formatted_object)+2
                output_lines.append(formatted_object)
        byte_offset -= 2
        self.object_byte_offset_map = object_byte_offset_map
        return '\n\n'.join(output_lines)

    def parse(self, io_buffer):
        # parse objects supplied by cross-reference table
        for subsection in self.pdf_section.crt_section.subsections:
            for entry in subsection.entries:
                entry_key = (entry.object_number, entry.generation_number)
                if entry.free is True:
                    entry.pdf_object = self.make_free_object(*entry_key)
                else:
                    io_buffer.seek(entry.first_item, io.SEEK_SET)
                    entry.pdf_object = PdfIndirectObject().parse(io_buffer)
                    if entry_key not in self.pdf_section.pdf_file.object_store:
                        self.pdf_section.pdf_file.object_store[entry_key] = entry.pdf_object
                    self.objects[entry_key] = entry.pdf_object
        return self


class CrtSection:

    _str = textwrap.dedent('''
        xref
        {subsections}
    ''').strip()
    _tokens = _str.encode('utf-8').split()

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section

        self.subsections = []

    def setup(self):
        # pdf file is being built from scratch, so create the basic objects
        self.subsections = [CrtSubsection(self.pdf_section)]
        self.add_pdf_object(self.pdf_section.body.zeroth_object)
        return self

    def add_pdf_object(self, pdf_object):
        subsection = self.subsections[-1]
        entry = CrtEntry(self.pdf_section)
        entry.pdf_object = pdf_object
        subsection.entries.append(entry)
        self.pdf_section.trailer.size += 1
        return entry

    def format(self):
        subsections = '\n'.join([subsection.format() for subsection in self.subsections])
        return self._str.format(subsections=subsections)

    def parse(self, io_buffer):
        _xref, _ = self._tokens
        lines = read_lines(io_buffer)
        next_line = next(lines, None)
        if next_line != _xref:
            raise PdfParseError
        while True:
            cur_offset = io_buffer.tell()
            next_token = next(read_pdf_tokens(io_buffer), None)
            io_buffer.seek(cur_offset, io.SEEK_SET)
            if next_token == b'trailer':
                break
            subsection = CrtSubsection(self.pdf_section).parse(io_buffer)
            self.subsections.append(subsection)

        return self


class CrtSubsection:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.entries = []

    def format(self):
        if len(self.entries) == 0:
            raise PdfFormatError
        first_object_number = self.entries[0].pdf_object.object_number
        output_lines = [f"{first_object_number} {len(self.entries)}"]
        output_lines.extend([entry.format() for entry in self.entries])
        return '\n'.join(output_lines)

    def parse(self, io_buffer):
        lines = read_lines(io_buffer)
        first_line = next(lines, None)
        if first_line is None:
            raise PdfParseError
        first_object_number, num_objects = first_line.split()
        for i in range(int(num_objects)):
            entry = CrtEntry(self.pdf_section).parse(io_buffer)
            entry.object_number = int(first_object_number)+i
            self.entries.append(entry)

        return self


class CrtEntry:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section

        self.pdf_object = None

        # set at parse-time
        self.object_number = None
        self.generation_number = None
        self.first_item = None
        self.free = None

    def format(self):
        if self.pdf_object is None:
            raise PdfFormatError
        if self.pdf_object.free is True:
            first_item = self.pdf_object.next_free_object.object_number
            generation_number = self.pdf_object.generation_number
            if generation_number != 65535:
                # next generation number should this object be used again
                generation_number += 1
        else:
            object_key = (self.pdf_object.object_number, self.pdf_object.generation_number)
            first_item = self.pdf_section.body.object_byte_offset_map[object_key]
            generation_number = self.pdf_object.generation_number
        return f"{first_item:010} {generation_number:05} {'f' if self.pdf_object.free is True else 'n'} "

    def parse(self, io_buffer):
        line = next(read_lines(io_buffer), None)
        if line is None:
            raise PdfParseError
        first_item, generation_number, usage_symbol = line.split()
        self.first_item = int(first_item)
        self.generation_number = int(generation_number)
        self.free = True if usage_symbol == b'f' else False
        return self


class FileTrailer:

    _str = textwrap.dedent('''
        trailer
        {trailer_dict}
        startxref
        {crt_byte_offset}
        %%EOF
    ''').strip()
    _tokens = _str.encode('utf-8').split()

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.size = PdfInteger()

        # set at format/parse-time
        self.crt_byte_offset = None

        # set at parse-time
        self.trailer_dict = None

    def format(self):
        pdf_dict = self.trailer_dict or PdfDict({
            PdfName('Root'): self.pdf_section.pdf_file.document_catalog.pdf_object.ref,
            PdfName('Size'): self.size,
        })
        trailer_dict = pdf_dict.format()
        return self._str.format(trailer_dict=trailer_dict, crt_byte_offset=self.crt_byte_offset)

    def parse(self, io_buffer):
        _trailer, _, _startxref, _, _eof = self._tokens
        next_token = next(read_pdf_tokens(io_buffer), None)
        if next_token != _trailer:
            raise PdfParseError

        self.trailer_dict = PdfDict().parse(io_buffer)
        if not isinstance(self.trailer_dict, PdfDict):
            raise PdfParseError
        self.size = self.trailer_dict['Size']

        next_token = next(read_pdf_tokens(io_buffer), None)
        if next_token != _startxref:
            raise PdfParseError

        lines = read_lines(io_buffer)
        next(lines, None)  # finish reading startxref line

        try:
            self.crt_byte_offset = int(next(lines, None))
        except ValueError:
            raise PdfParseError

        if next(lines, None) != _eof:
            raise PdfParseError

        return self


class DocumentCatalog:

    def __init__(self,
            pdf_file,
            version=None,
            # page_label_tree=None,
            page_layout=None,
            # outline_hierarchy=None,
            # article_threads=None,
            # named_destinations=None,
            # interactive_form=None,
            ):
        self.pdf_file = pdf_file
        self.page_tree = None

        self.pdf_object = None

        self.version, _ = get_optional_entry('version', version)
        # self.page_label_tree = page_label_tree
        self.page_layout, _ = get_optional_entry('page_layout', page_layout)
        # self.page_mode = None
        # self.outline_hierarchy = outline_hierarchy
        # self.article_threads = article_threads
        # self.named_destinations = named_destinations
        # self.interactive_form = interactive_form

    def __repr__(self):
        return f'{self.__class__.__name__}({self.page_tree}, version={self.version}, page_layout={self.page_layout})'

    def setup(self):
        self.page_tree = PageTreeNode(self.pdf_file).setup()
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfDict({
                PdfName('Type'):  PdfName('Catalog'),
                PdfName('Pages'): self.page_tree.pdf_object.ref,
            })
        )
        return self

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        page_tree_ref = pdf_object.contents['Pages']
        object_key = (page_tree_ref.object_number, page_tree_ref.generation_number)
        if object_key not in self.pdf_file.object_store:
            raise PdfParseError
        page_tree_object = self.pdf_file.object_store[object_key]
        self.page_tree = PageTreeNode(self.pdf_file).from_object(page_tree_object)
        return self


class PageTreeNode:

    def __init__(self, pdf_file, parent=None):
        self.pdf_file = pdf_file
        self.parent = parent
        self.children = []

        self.pdf_object = None

        self.kids = PdfArray()
        self.count = PdfInteger()

        # inheritable properties
        self.resources = None
        self.media_box = None

    def setup(self):
        self.resources = PdfDict({PdfName('Font'): PdfDict()})
        media_box, _ = get_optional_entry('media_box', None)
        self.media_box = PdfArray(map(PdfInteger, media_box))
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfDict({
                PdfName('Type'):  PdfName('Pages'),
                PdfName('Kids'): self.kids,
                PdfName('Count'): self.count,
            })
        )
        return self

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        self.resources = pdf_object.contents.get('Resources')
        self.kids = pdf_object.contents['Kids']
        for kid_ref in pdf_object.contents['Kids']:
            object_key = (kid_ref.object_number, kid_ref.generation_number)
            if object_key not in self.pdf_file.object_store:
                raise PdfParseError
            kid_object = self.pdf_file.object_store[object_key]
            if kid_object.contents['Type'] == 'Pages':
                self.children.append(PageTreeNode(self.pdf_file, parent=self).from_object(kid_object))
            elif kid_object.contents['Type'] == 'Page':
                self.children.append(PageObject(self.pdf_file, self).from_object(kid_object))
            else:
                raise PdfParseError
            self.count += 1
        return self

    def add_page(self):
        page = PageObject(self.pdf_file, self).setup()
        self.children.append(page)
        self.kids.append(page.pdf_object.ref)
        self.count += 1
        return page


class PageObject:

    def __init__(self, pdf_file, parent):
        self.pdf_file = pdf_file
        self.parent = parent
        self.objects = []

        self.pdf_object = None

        self.resources = None
        self.media_box = None
        self.contents = None
        self.font_number = 0

    def setup(self):
        self.resources = get_inherited_entry('resources', self, required=True)
        self.media_box = get_inherited_entry('media_box', self, required=True)
        self.contents = PdfArray()
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfDict({
                PdfName('Type'):  PdfName('Page'),
                PdfName('Parent'): self.parent.pdf_object.ref,
                PdfName('Resources'): self.resources,
                PdfName('MediaBox'): self.media_box
            })
        )
        return self

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        self.resources = pdf_object.contents.get('Resources')
        self.resources = get_inherited_entry('resources', self, required=True)
        self.media_box = pdf_object.contents.get('MediaBox')
        self.media_box = get_inherited_entry('media_box', self, required=True)
        self.contents = pdf_object.contents.get('Contents', PdfArray())
        for content_ref in self.contents:
            if content_ref.object_key not in self.pdf_file.object_store:
                raise PdfParseError
            content_object = self.pdf_file.object_store[content_ref.object_key]
            self.objects.append(ContentStream(self.pdf_file).from_object(content_object))
        max_font_number = 0
        for font_alias_name in self.resources.get('Font', {}):
            font_number = int(font_alias_name[1:])
            max_font_number = max(font_number, max_font_number)
        self.font_number = max_font_number
        return self

    def add_font(self, font_name):
        font_name, font_settings = get_optional_entry('font', font_name)
        sub_type = PdfName(font_settings['sub_type'])
        font_name = PdfName(font_name)
        if font_name not in self.pdf_file.fonts:
            font = Font(self.pdf_file, font_name, sub_type).setup()
            self.pdf_file.fonts[font_name] = self
        else:
            font = self.pdf_file.fonts[font_name]
        self.font_number += 1
        font_alias_name = PdfName(f'F{self.font_number}')
        self.resources['Font'][font_alias_name] = font.pdf_object.ref
        return font_alias_name

    def add_content_stream(self, contents):
        stream = ContentStream(self.pdf_file, contents=contents).setup()
        if 'Contents' not in self.pdf_object.contents:
            self.pdf_object.contents[PdfName('Contents')] = self.contents
        self.contents.append(stream.pdf_object.ref)
        self.objects.append(stream)
        return stream

    def add_text(self, text, font_name=None, size=None, line_size=None,
            translate_x=None, translate_y=None, scale_x=None, scale_y=None,
            skew_angle_a=None, skew_angle_b=None, rotation_angle=None):
        font_alias_name = self.add_font(font_name)
        text_obj = StreamTextObject(contents=[
            TextMatrixOperation(),
            TextFontOperation(font_alias_name=font_alias_name, size=size),
            TextLeadingOperation(leading=line_size),
            TextShowOperation(text=text),
            TextNextLineOperation()
        ])
        cm = ConcatenateMatrixOperation()
        if translate_x is not None or translate_y is not None:
            cm.add_translation(x=translate_x, y=translate_y)
        if rotation_angle is not None:
            cm.add_rotation(rotation_angle)
        if scale_x is not None or scale_y is not None:
            cm.add_scaling(x=scale_x, y=scale_y)
        if skew_angle_a is not None or skew_angle_b is not None:
            cm.add_skew(angle_a=skew_angle_a, angle_b=skew_angle_b)
        self.add_content_stream([
            StateSaveOperation(),
            cm,
            text_obj,
            StateRestoreOperation()
        ])
        return text_obj


class Font:

    def __init__(self, pdf_file, font_name, sub_type):
        self.pdf_file = pdf_file
        self.font_name = font_name
        self.sub_type = sub_type

        self.pdf_object = None

    def setup(self):
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfDict({
                PdfName('Type'):  PdfName('Font'),
                PdfName('Subtype'): self.sub_type,
                PdfName('BaseFont'): self.font_name,
            })
        )
        return self


class ContentStream:

    def __init__(self, pdf_file, contents=None):
        self.pdf_file = pdf_file
        self.contents = contents

        self.pdf_object = None

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        stream = pdf_object.contents
        self.contents = stream.contents
        return self

    def setup(self):
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfStream(contents=self.contents)
        )
        return self
