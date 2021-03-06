import io
import math

from PIL import Image

from pdfalcon.exceptions import PdfIoError, PdfBuildError, PdfFormatError, PdfParseError
from pdfalcon.types import PdfArray, PdfDict, PdfIndirectObject, PdfInteger, PdfName, PdfReal, PdfStream, PdfLiteralString, \
    ConcatenateMatrixOperation, StateRestoreOperation, StateSaveOperation, StreamTextObject, StreamXObject, StreamPathObject, \
    TextFontOperation, TextLeadingOperation, TextMatrixOperation, TextNextLineOperation, TextShowOperation, \
    PathMoveOperation, PathCurveOperation, PathCloseOperation, PathStrokeOperation, PathFillOperation, \
    PathFillEvenOddOperation, PathFillStrokeOperation, PathFillEvenOddStrokeOperation
from pdfalcon.options import get_inherited_entry, get_optional_entry
from pdfalcon.parsing import read_lines, read_pdf_tokens, reverse_read_lines


class PdfFile:
    """
    The idea of the PdfFile is two-fold:
     1. provide an abstract representation of a pdf file as defined by the PDF spec
        (therefore effectively representing an abstract syntax tree)
     2. provide an interface for manipulating pdf files

    a consideration for the future would be to make interface mixin classes to extend
        from, if not just to modularize high-level / client functionality
    """

    def __init__(self, version=None, setup=True):
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

        # all in-use objects
        self.object_store = {}

        self.cur_format_byte_offset = None

        if setup is True:
            self.setup()

    def __bytes__(self):
        # convert the pdf file object to pdf syntax
        if len(self.sections) == 0:
            raise PdfBuildError

        output_lines = []
        formatted_header = bytes(self.header)
        self.cur_format_byte_offset = len(formatted_header)+2
        output_lines.append(formatted_header)

        for i, section in enumerate(self.sections):
            if i > 0:
                section.trailer.prev = self.sections[i-1].trailer.crt_byte_offset
            formatted_section = bytes(section)
            self.cur_format_byte_offset += 2
            output_lines.append(formatted_section)
        self.cur_format_byte_offset -= 2

        return b'\n\n'.join(output_lines)

    @property
    def pages(self):
        return self.document_catalog.page_tree.get_pages()

    def setup(self):
        # builds a basic structure
        self.header = FileHeader(self)
        section = FileSection(self).setup()
        self.sections = [section]

        self.document_catalog = DocumentCatalog(self).setup()
        return self

    def parse(self, io_buffer):
        # create pdf file and document structures from pdf syntax
        self.header = FileHeader(self)
        self.header.parse(io_buffer)

        while True:
            if len(self.sections) > 0 and self.sections[0].trailer.prev is None:
                break
            file_section = FileSection(self).parse(io_buffer)
            self.sections.insert(0, file_section)

        root = self.sections[-1].trailer.root
        object_key = (root.object_number, root.generation_number)
        if object_key not in self.object_store:
            raise PdfParseError
        pdf_object = self.object_store[object_key]
        self.document_catalog = DocumentCatalog(self).from_object(pdf_object)
        return self

    def add_update(self):
        if len(self.sections) == 0:
            raise PdfBuildError
        file_update = FileSection(self).setup()
        self.sections.append(file_update)
        return file_update

    def add_pdf_object(self, contents):
        pdf_object = PdfIndirectObject()
        max_object_number, _ = sorted(self.object_store)[-1] if len(self.object_store) > 0 else (0, None)
        object_number = max_object_number + 1
        generation_number = 0
        section_number = len(self.sections)-1
        pdf_section = self.sections[section_number]
        pdf_object.attach(object_number, generation_number, pdf_section, contents)
        self.object_store[pdf_object.object_key] = pdf_object
        return pdf_section.add_pdf_object(pdf_object)

    def update_pdf_object(self, pdf_object, new_contents):
        section_number = len(self.sections)-1
        pdf_section = self.sections[section_number]
        if pdf_object.pdf_section is pdf_section:
            raise PdfBuildError
        new_pdf_object = PdfIndirectObject()
        new_pdf_object.attach(*pdf_object.object_key, pdf_section, new_contents)
        self.object_store[new_pdf_object.object_key] = new_pdf_object
        return pdf_section.add_pdf_object(new_pdf_object)

    def release_pdf_object(self, pdf_object):
        if pdf_object.object_key not in self.object_store:
            raise PdfBuildError
        pdf_section = self.sections[section_number]
        pdf_object = self.pdf_section.release_pdf_object(pdf_object)
        del self.object_store[pdf_object.object_key]
        return pdf_object

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
        io_buffer.write(bytes(self))

    @classmethod
    def read(cls, io_buffer):
        # validate and parse an io buffer
        if not isinstance(io_buffer, io.IOBase):
            raise PdfIoError
        if not io_buffer.seekable():
            raise PdfIoError
        return cls(setup=False).parse(io_buffer)

    def merge(self, pdf):
        self.add_update()
        self.version = max(self.version, pdf.version)
        target_pages = self.pages
        for i, page in enumerate(pdf.pages):
            if i >= len(target_pages):
                target_page = self.add_page()
            else:
                target_page = target_pages[i]
            for content_stream in page.objects:
                new_contents = [c.clone() for c in content_stream.contents]
                target_page.add_content_stream(new_contents)
            for font_ref in page.resources['Font'].values():
                pdf_object = pdf.object_store[font_ref.object_key]
                target_page.add_font(pdf_object.contents['BaseFont'].value)
        return self

    def clone(self):
        return self.__class__().merge(self)


class FileHeader:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

    def __bytes__(self):
        return b'\n'.join([
            b'%%PDF-%b' % bytes(PdfReal(self.pdf_file.version)),
            b'\xc3\xa2\xc3\xa3\xc3\x8f\xc3\x93'
        ])

    def parse(self, io_buffer):
        io_buffer.seek(0, io.SEEK_SET)
        lines = read_lines(io_buffer)
        first_line = next(lines, None)
        if first_line is None:
            raise PdfParseError
        if first_line.startswith(b'%PDF-') is False:
            raise PdfParseError
        self.pdf_file.version = float(first_line[5:])
        return self


class FileSection:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

        self.body = None
        self.crt_section = None
        self.trailer = None

    def __bytes__(self):
        body_bytes = bytes(self.body)
        self.pdf_file.cur_format_byte_offset += len(body_bytes)+2
        self.trailer.crt_byte_offset = self.pdf_file.cur_format_byte_offset

        crt_section_bytes = bytes(self.crt_section)
        self.pdf_file.cur_format_byte_offset += len(crt_section_bytes)+2

        trailer_bytes = bytes(self.trailer)
        self.pdf_file.cur_format_byte_offset += len(trailer_bytes)

        return b'\n\n'.join([body_bytes, crt_section_bytes, trailer_bytes])

    def setup(self):
        self.body = FileBody(self)
        self.crt_section = CrtSection(self)
        self.trailer = FileTrailer(self)

        self.body.setup()
        self.crt_section.setup()
        return self

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
            prev_crt_byte_offset = self.pdf_file.sections[0].trailer.prev
            io_buffer.seek(prev_crt_byte_offset, io.SEEK_SET)
            self.crt_section.parse(io_buffer)
            self.trailer.parse(io_buffer)
            self.body.parse(io_buffer)

        return self

    def add_pdf_object(self, pdf_object):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.crt_section.add_pdf_object(pdf_object)
        return pdf_object, entry

    def release_pdf_object(self, pdf_object):
        pdf_object = self.body.release_pdf_object(pdf_object)
        return pdf_object


class FileBody:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.objects = {}

        self.zeroth_object = None
        self.free_object_list_tail = None

        self.object_byte_offset_map = None

    def __bytes__(self):
        byte_offset = self.pdf_section.pdf_file.cur_format_byte_offset
        output_lines = []
        object_byte_offset_map = {}
        for k in sorted(self.objects):
            pdf_object = self.objects[k]
            if pdf_object.attached is True and pdf_object.object_number != 0:
                obj_bytes = bytes(pdf_object)
                object_byte_offset_map[pdf_object.object_key] = byte_offset
                byte_offset += len(obj_bytes)+2
                output_lines.append(obj_bytes)
        byte_offset -= 2
        self.object_byte_offset_map = object_byte_offset_map
        return b'\n\n'.join(output_lines)

    def setup(self):
        # start with zeroth object
        object_number, generation_number = 0, 65535
        pdf_object = self.make_free_object(object_number, generation_number)
        return self

    def parse(self, io_buffer):
        # parse objects supplied by cross-reference table
        for subsection in self.pdf_section.crt_section.subsections:
            for entry in subsection.entries:
                if entry.free is True:
                    entry.pdf_object = self.make_free_object(*entry.object_key)
                else:
                    io_buffer.seek(entry.first_item, io.SEEK_SET)
                    entry.pdf_object = PdfIndirectObject().parse(io_buffer)
                    entry.pdf_object.pdf_section = self.pdf_section
                    self.add_pdf_object(entry.pdf_object)
                    if entry.object_key not in self.pdf_section.pdf_file.object_store:
                        self.pdf_section.pdf_file.object_store[entry.object_key] = entry.pdf_object
        return self

    def make_free_object(self, object_number, generation_number):
        pdf_object = PdfIndirectObject()
        if self.zeroth_object is None:
            if (object_number, generation_number) != (0, 65535):
                raise PdfBuildError
            self.zeroth_object = pdf_object
            self.free_object_list_tail = pdf_object
        pdf_object.attach(object_number, generation_number, self.pdf_section, None)
        self.add_pdf_object(pdf_object)
        self.release_pdf_object(pdf_object)
        return pdf_object

    def add_pdf_object(self, pdf_object):
        self.objects[pdf_object.object_key] = pdf_object
        return pdf_object

    def release_pdf_object(self, pdf_object):
        # produces a free object
        pdf_object.release(self.zeroth_object)

        # set previous tail's next free object
        self.free_object_list_tail.next_free_object = pdf_object

        # set new tail
        self.free_object_list_tail = pdf_object

        return pdf_object


class CrtSection:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section

        self.subsections = []

    def setup(self):
        # pdf file is being built from scratch, so create the basic objects
        self.add_pdf_object(self.pdf_section.body.zeroth_object)
        return self

    def add_pdf_object(self, pdf_object):
        entry = CrtEntry(self.pdf_section)
        entry.pdf_object = pdf_object
        found_subsection = False
        subsection_placement_index = None
        for i, subsection in enumerate(self.subsections):
            first_object_number = subsection.entries[0].pdf_object.object_number
            last_object_number = subsection.entries[-1].pdf_object.object_number
            if pdf_object.object_number == first_object_number-1:
                subsection.entries.insert(0, entry)
                found_subsection = True
                break
            if pdf_object.object_number == last_object_number+1:
                subsection.entries.append(entry)
                found_subsection = True
                break
            if subsection_placement_index is None and pdf_object.object_number < first_object_number-1:
                subsection_placement_index = i
        if found_subsection is False:
            subsection_placement_index = subsection_placement_index or len(self.subsections)
            new_subsection = CrtSubsection(self.pdf_section)
            new_subsection.entries.append(entry)
            self.subsections.insert(subsection_placement_index, new_subsection)
        self.pdf_section.trailer.size += 1
        return entry

    def __bytes__(self):
        return b'\n'.join([
            b'xref',
            *map(bytes, self.subsections)
        ])

    def parse(self, io_buffer):
        lines = read_lines(io_buffer)
        next_line = next(lines, None)
        if next_line != b'xref':
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

    def __bytes__(self):
        if len(self.entries) == 0:
            raise PdfFormatError
        return b'\n'.join([
            b' '.join([
                bytes(PdfInteger(self.entries[0].pdf_object.object_number)),
                bytes(PdfInteger(len(self.entries)))
            ]),
            *map(bytes, self.entries)
        ])

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

        self.object_number = None
        self.generation_number = None
        self.first_item = None
        self.free = None

    @property
    def object_key(self):
        return (self.object_number, self.generation_number)

    def __bytes__(self):
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
        return b' '.join([
            bytes(PdfInteger(first_item)).zfill(10),
            bytes(PdfInteger(generation_number)).zfill(5),
            b'f ' if self.pdf_object.free is True else b'n '
        ])

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

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section

        self.crt_byte_offset = None
        self.size = 0
        self.root = None
        self.prev = None

    def __bytes__(self):
        trailer_dict = PdfDict({
            PdfName('Root'): self.root or self.pdf_section.pdf_file.document_catalog.pdf_object.ref,
            PdfName('Size'): PdfInteger(self.size)
        })
        if self.prev:
            trailer_dict[PdfName('Prev')] = PdfInteger(self.prev)
        return b'\n'.join([
            b'trailer',
            bytes(trailer_dict),
            b'startxref',
            bytes(PdfInteger(self.crt_byte_offset)),
            b'%%EOF'
        ])

    def parse(self, io_buffer):
        next_token = next(read_pdf_tokens(io_buffer), None)
        if next_token != b'trailer':
            raise PdfParseError

        trailer_dict = PdfDict().parse(io_buffer)
        if not isinstance(trailer_dict, PdfDict):
            raise PdfParseError
        self.size = int(trailer_dict['Size'])
        self.root = trailer_dict['Root']
        self.prev = int(trailer_dict['Prev']) if 'Prev' in trailer_dict else None

        next_token = next(read_pdf_tokens(io_buffer), None)
        if next_token != b'startxref':
            raise PdfParseError

        lines = read_lines(io_buffer)
        next(lines, None)  # finish reading startxref line

        try:
            self.crt_byte_offset = int(next(lines, None))
        except ValueError as e:
            raise PdfParseError from e

        if next(lines, None) != b'%%EOF':
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
                PdfName('Kids'): PdfArray(),
                PdfName('Count'): PdfInteger(),
            })
        )
        return self

    def get_pages(self):
        pages = []
        for child in self.children:
            if isinstance(child, PageObject):
                pages.append(child)
            else:
                pages.extend(child.get_pages())
        return pages

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        self.resources = pdf_object.contents.get('Resources')
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
        return self

    def add_page(self):
        page = PageObject(self.pdf_file, self).setup()
        self.children.append(page)
        if self.pdf_object.pdf_section is not self.pdf_file.sections[-1]:
            self.pdf_object, _ = self.pdf_file.update_pdf_object(self.pdf_object, self.pdf_object.contents.clone())
        self.pdf_object.contents['Kids'].append(page.pdf_object.ref)
        self.pdf_object.contents['Count'] += 1
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
        self.image_number = 0

    def setup(self):
        self.resources = get_inherited_entry('resources', self, required=True)
        self.resources[PdfName('ProcSet')] = PdfArray(set(self.resources.get('ProcSet', PdfArray()) + PdfArray([PdfName('PDF')])))
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

    def add_image_xobject(self, io_buffer):
        im = Image.open(io_buffer)

        bits = PdfInteger(8)
        if im.mode == "1":
            filter_type = PdfName("ASCIIHexDecode")
            colorspace = PdfName("DeviceGray")
            procset = PdfName("ImageB")  # grayscale
            bits = PdfInteger(1)
        elif im.mode == "L":
            filter_type = PdfName("DCTDecode")
            colorspace = PdfName("DeviceGray")
            procset = PdfName("ImageB")  # grayscale
        elif im.mode == "P":
            filter_type = PdfName("ASCIIHexDecode")
            palette = im.im.getpalette("RGB")
            colorspace = PdfArray([
                PdfName("Indexed"),
                PdfName("DeviceRGB"),
                PdfInteger(255),
                PdfBinary(palette),
            ])
            procset = PdfName("ImageI")  # indexed color
        elif im.mode == "RGB":
            filter_type = PdfName("DCTDecode")
            colorspace = PdfName("DeviceRGB")
            procset = PdfName("ImageC")  # color images
        elif im.mode == "CMYK":
            filter_type = PdfName("DCTDecode")
            colorspace = PdfName("DeviceCMYK")
            procset = PdfName("ImageC")  # color images
        else:
            raise PdfBuildError(f"cannot save mode {im.mode}")

        width, height = map(PdfReal, im.size)

        io_buffer.seek(0, io.SEEK_SET)

        image_xobject, _ = self.pdf_file.add_pdf_object(
            PdfStream(
                contents=[io_buffer.read()],
                stream_dict=PdfDict({
                    PdfName('Type'): PdfName("XObject"),
                    PdfName('Subtype'): PdfName("Image"),
                    PdfName('Width'): width,
                    PdfName('Height'): height,
                    PdfName('Filter'): filter_type,
                    PdfName('BitsPerComponent'): bits,
                    PdfName('ColorSpace'): colorspace,
                })
            )
        )
        self.image_number += 1
        image_alias_name = PdfName(f'Im{self.image_number}')
        self.resources.setdefault(PdfName('XObject'), PdfDict())[image_alias_name] = image_xobject.ref
        self.resources[PdfName('ProcSet')] = PdfArray(set(self.resources.get('ProcSet', PdfArray()) + PdfArray([procset])))
        return image_alias_name, im

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
        cm = ConcatenateMatrixOperation()
        if translate_x is not None or translate_y is not None:
            cm.add_translation(x=translate_x, y=translate_y)
        if rotation_angle is not None:
            cm.add_rotation(rotation_angle)
        if scale_x is not None or scale_y is not None:
            cm.add_scaling(x=scale_x, y=scale_y)
        if skew_angle_a is not None or skew_angle_b is not None:
            cm.add_skew(angle_a=skew_angle_a, angle_b=skew_angle_b)
        content_stream = self.add_content_stream([
            StateSaveOperation(),
            cm,
            StreamTextObject(contents=[
                TextMatrixOperation(),
                TextFontOperation(font_alias_name=font_alias_name, size=size),
                TextLeadingOperation(leading=line_size),
                TextShowOperation(text=text),
                TextNextLineOperation()
            ]),
            StateRestoreOperation()
        ])
        return content_stream

    def add_image(self, io_buffer, resolution=72.0,
            translate_x=None, translate_y=None, scale_x=None, scale_y=None,
            skew_angle_a=None, skew_angle_b=None, rotation_angle=None):
        image_alias_name, im = self.add_image_xobject(io_buffer)
        cm = ConcatenateMatrixOperation()
        if translate_x is not None or translate_y is not None:
            cm.add_translation(x=translate_x, y=translate_y)
        if rotation_angle is not None:
            cm.add_rotation(rotation_angle)
        if scale_x is not None or scale_y is not None:
            cm.add_scaling(x=scale_x, y=scale_y)
        else:
            width, height = im.size
            cm.add_scaling(x=int(width * 72.0 / resolution), y=int(height * 72.0 / resolution))
        if skew_angle_a is not None or skew_angle_b is not None:
            cm.add_skew(angle_a=skew_angle_a, angle_b=skew_angle_b)
        content_stream = self.add_content_stream([
            StateSaveOperation(),
            cm,
            StreamXObject(alias_name=image_alias_name),
            StateRestoreOperation()
        ])
        return content_stream

    @staticmethod
    def _compute_bounded_bezier_path(x1, y1, x2, y2, start_angle=0, extent=90):
        curve_count = int(math.ceil(abs(extent)/90.0))
        curve_angle = float(extent) / curve_count

        x_cen = (x1+x2)/2.0
        y_cen = (y1+y2)/2.0
        x_dist = abs(x2-x1)/2.0
        y_dist = abs(y2-y1)/2.0

        h = curve_angle * math.pi / 360.0
        kappa = abs(4.0 / 3.0 * (1.0 - math.cos(h)) / math.sin(h))

        sign = 1 if curve_angle > 0 else -1

        curves = []
        for i in range(curve_count):
            theta0 = (start_angle + i*curve_angle) * math.pi / 180.0
            theta1 = (start_angle + (i+1)*curve_angle) * math.pi / 180.0

            curves.append((
                x_cen + x_dist * math.cos(theta0),
                y_cen - y_dist * math.sin(theta0),
                x_cen + x_dist * (math.cos(theta0) - sign * kappa * math.sin(theta0)),
                y_cen - y_dist * (math.sin(theta0) + sign * kappa * math.cos(theta0)),
                x_cen + x_dist * (math.cos(theta1) + sign * kappa * math.sin(theta1)),
                y_cen - y_dist * (math.sin(theta1) - sign * kappa * math.cos(theta1)),
                x_cen + x_dist * math.cos(theta1),
                y_cen - y_dist * math.sin(theta1)
            ))

        return curves

    def add_ellipse(self, x, y, width, height, fill=False, stroke=True, fill_rule='nonzero-winding-number'):
        curves = self._compute_bounded_bezier_path(x, y, x+width, y+height, extent=360)
        start_x,start_y,*_ = curves[0]
        content_stream = self.add_content_stream([
            StreamPathObject(contents=[
                PathMoveOperation(x=start_x,y=start_y),
                *[PathCurveOperation(*curve) for _,_,*curve in curves],
                PathCloseOperation(),
                PathStrokeOperation()
            ])
        ])
        return content_stream

    def add_circle(self, x, y, r):
        return self.add_ellipse(x-r, y-r, 2*r, 2*r)


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

    def __init__(self, pdf_file, contents=None, filters=None):
        self.pdf_file = pdf_file
        self.contents = contents
        self.filters = filters or ['ASCII85Decode', 'FlateDecode']

        self.pdf_object = None

    def from_object(self, pdf_object):
        self.pdf_object = pdf_object
        stream = pdf_object.contents
        self.contents = stream.contents
        return self

    def setup(self):
        stream_dict = PdfDict({
            PdfName('Filter'): PdfArray([PdfName(f) for f in self.filters])
        })
        self.pdf_object, _ = self.pdf_file.add_pdf_object(
            PdfStream(contents=self.contents, stream_dict=stream_dict)
        )
        return self
