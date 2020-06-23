import io
import math
import numpy as np


OPTIONS = {
    'version': {
        'default': '1.4',
        'options': {'1.4': {}, '1.5': {}, '1.6': {}, '1.7': {}}
    },
    'page_layout': {
        'default': 'single_page',
        'options': {
            'single_page': {},      # Display one page at a time
            'one_column': {},       # Display the pages in one column 
            'two_column_left': {},  # Display the pages in two columns, with odd-numbered pages on the left
            'two_column_right': {}, # Display the pages in two columns, with odd-numbered pages on the right
            'two_page_left': {},    # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the left
            'two_page_right': {},   # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the right
        }
    },
    'media_box': {
        'default': [0, 0, 612, 792]
    },
    'font': {
        'default': 'Helvetica',
        'options': {
            'Times-Roman': {
                'sub_type': 'Type1'
            },
            'Helvetica': {
                'sub_type': 'Type1'
            },
            'Courier': {
                'sub_type': 'Type1'
            },
            'Symbol': {
                'sub_type': 'Type1'
            },
            'Times-Bold': {
                'sub_type': 'Type1'
            },
            'Helvetica-Bold': {
                'sub_type': 'Type1'
            },
            'Courier-Bold': {
                'sub_type': 'Type1'
            },
            'ZapfDingbats': {
                'sub_type': 'Type1'
            },
            'Times-Italic': {
                'sub_type': 'Type1'
            },
            'Helvetica-Oblique': {
                'sub_type': 'Type1'
            },
            'Courier-Oblique': {
                'sub_type': 'Type1'
            },
            'Times-BoldItalic': {
                'sub_type': 'Type1'
            },
            'Helvetica-BoldOblique': {
                'sub_type': 'Type1'
            },
            'Courier-BoldOblique': {
                'sub_type': 'Type1'
            },
        }
    },
}


class PdfBuildError(Exception):
    pass


class PdfFormatError(Exception):
    pass


class PdfParseError(Exception):
    pass


class PdfValueError(ValueError):
    pass


def get_optional_entry(key, val):
    if val is None:
        if 'default' not in OPTIONS[key]:
            raise PdfBuildError
        else:
            val = OPTIONS[key]['default']
    if 'options' in OPTIONS[key] and val not in OPTIONS[key]['options']:
        raise PdfBuildError
    settings = OPTIONS[key]['options'][val] if 'options' in OPTIONS[key] else {}
    return val, settings


def get_inherited_entry(key, node, required=False):
    val = getattr(node, key)
    if val is None:
        if node.parent is not None:
            val = get_inherited_entry(key, node.parent)
        if val is None and required is True:
            raise PdfBuildError
    return val


def format_type(data):
    if isinstance(data, list):
        return f"[ {' '.join([format_type(x) for x in data])} ]"
    elif isinstance(data, dict):
        output_lines = [
            '<<',
            *[f"{k} {format_type(v)}" for k,v in data.items()],
            '>>'
        ]
        return '\n'.join(output_lines)
    elif isinstance(data, int) or isinstance(data, float):
        return str(data)
    elif isinstance(data, str):
        return data
    else:
        # unrecognized type
        raise PdfFormatError


def parse_type(io_buffer):
    # parser for the basic delimited types, maintains buffer position
    # 
    # types: Boolean values, Integer and Real numbers, Strings, Names, Arrays,
    #   Dictionaries, Streams, and the null object
    tokens = read_pdf_tokens(io_buffer)
    first_token = next(tokens, None)
    if first_token is None:
        # there must be something there! besides whitespace
        raise PdfParseError
    elif first_token == b'<':
        next_token = next(tokens, None)
        if next_token == b'<':
            # dictionary type
            result = {}
            while True:
                key = parse_type(io_buffer)
                if key == b'>':
                    end_token = next(tokens, None)
                    if end_token != b'>':
                        raise PdfParseError
                    break
                value = parse_type(io_buffer)
                result[key] = value

            dict_end_offset = io_buffer.tell()
            dict_post_token = next(tokens, None)
            if dict_post_token == b'stream':
                # stream type
                stream_contents = []
                while True:
                    stream_token = next(tokens, None)
                    if stream_token == b'endstream':
                        break
                    # TODO: parse stream objects, determine if ContentStream,
                    #   apply decoding filters, determine handling of special whitespace

                return PdfStream(result, stream_contents)
            else:
                io_buffer.seek(dict_end_offset, io.SEEK_SET)
                return PdfDict(result)
        else:
            # hex string type
            hex_string = b''
            if next_token != '>':
                hex_string = next_token
                while True:
                    next_token = next(tokens, None)
                    if next_token == b'>':
                        break
                    hex_string += next_token
                if len(hex_string) % 2 != 0:
                    # last zero is assumed if odd number of chars
                    hex_string += b'0'
            return PdfHexString(hex_string)
    elif first_token == b'[':
        # array type
        result = []
        while True:
            item = parse_type(io_buffer)
            if item == b']':
                break
            result.append(item)
        return result
    elif first_token == b'{':
        # function expression type
        pass
    elif first_token == b'(':
        # string literal type
        pass
    elif first_token == b'true':
        # boolean type
        return PdfBool(True)
    elif first_token == b'false':
        # boolean type
        return PdfBool(False)
    elif first_token == b'null':
        # null type
        return PdfNull(None)
    elif first_token == b'%':
        # comment type
        comment_line = next(read_lines(io_buffer))
        # why tho
    elif first_token == b'/':
        # name type
        solidus_end_offset = io_buffer.tell()
        name = next(tokens, None)
        name_end_offset = io_buffer.tell()
        if solidus_end_offset != name_end_offset-len(name):
            # no whitespace allowed between solidus and name
            raise ParseError
        return PdfName(name)
    else:
        try:
            first_token = float(first_token)
        except ValueError:
            # unrecognized type
            raise PdfParseError
        return PdfNumeric(first_token)


def read_pdf_tokens(io_buffer):
    # defined by PDF spec
    whitespace_chars = b'\x00\t\n\x0c\r '

    # defined by PDF spec
    delimiters = b'()<>[]{}/%'

    # return the generator
    return read_tokens(io_buffer, whitespace_chars, delimiters)


def read_tokens(io_buffer, whitespace_chars, delimiters, block_size=64):
    # read tokens (i.e. whitespace-delimited words), one block of bytes at a time
    cur_token = b''
    while True:
        block_offset = io_buffer.tell()
        block = io_buffer.read(block_size)
        if not block:
            break

        # send cursor back, it'll be advanced one token at a time
        io_buffer.seek(block_offset, io.SEEK_SET)

        token_end_offset = 0
        for char in block:
            char = b'%c' % char  # convert raw byte to byte str
            if char in delimiters:
                if cur_token != b'':
                    # end of token
                    io_buffer.seek(token_end_offset, io.SEEK_CUR)
                    yield cur_token
                io_buffer.seek(1, io.SEEK_CUR)
                yield char
                cur_token = b''
                token_end_offset = 0
            elif char in whitespace_chars:
                if cur_token != b'':
                    # end of token
                    io_buffer.seek(token_end_offset, io.SEEK_CUR)
                    yield cur_token

                    cur_token = b''
                    token_end_offset = 0
                else:
                    token_end_offset += 1
            else:
                cur_token += char
                token_end_offset += 1

    io_buffer.seek(token_end_offset, io.SEEK_CUR)
    yield cur_token


def read_lines(io_buffer, block_size=64*1024):
    # read lines one block of bytes at a time
    line_remainder = b''
    while True:
        block_offset = io_buffer.tell()
        block = io_buffer.read(block_size)
        if not block:
            break

        # send cursor back, it'll be advanced one line at a time
        io_buffer.seek(block_offset, io.SEEK_SET)

        lines = block.splitlines(keepends=True)

        # the last line of each block is left for the subsequent
        # block to process
        if line_remainder:
            lines[0] = line_remainder + lines[0]
        line_remainder = lines.pop(-1)

        for line in lines:
            io_buffer.seek(len(line), io.SEEK_CUR)
            yield line.strip()

    io_buffer.seek(len(line_remainder), io.SEEK_CUR)
    yield line_remainder.strip()


def reverse_read_lines(io_buffer, block_size=64*1024):
    # read lines in reverse one block of bytes at a time
    byte_offset = io_buffer.tell()
    line_remainder = b''
    while byte_offset > 0:
        read_size = min(byte_offset, block_size)
        byte_offset -= read_size
        io_buffer.seek(byte_offset, io.SEEK_SET)
        block = io_buffer.read(read_size)

        lines = block.splitlines(keepends=True)

        # the first line of each block is left for the subsequent
        # block to process
        if line_remainder:
            lines[-1] += line_remainder
        line_remainder = lines.pop(0)

        for line in lines[::-1]:
            yield line.strip()
            io_buffer.seek(-len(line), io.SEEK_CUR)

    yield line_remainder.strip()


class PdfObject:
    pass


class PdfHexString(PdfObject):

    def __init__(self, value):
        try:
            # validate hexadecimal input
            int(value, 16)
        except ValueError:
            raise PdfValueError
        self.value = value


class PdfDict(PdfObject):

    def __init__(self, value):
        if not isinstance(value, dict):
            raise PdfValueError
        self.value = value


class PdfStream(PdfObject):

    def __init__(self, stream_dict, stream_contents):
        if not isinstance(stream_dict, dict):
            raise PdfValueError
        self.stream_dict = stream_dict
        self.stream_contents = stream_contents


class PdfName(PdfObject):

    def __init__(self, value):
        # TODO: improve validation to be based on spec
        if not isinstance(value, str):
            raise PdfValueError
        self.value = value


class PdfNull(PdfObject):

    def __init__(self, value):
        if value is not None:
            raise PdfValueError
        self.value = value


class PdfBool(PdfObject):

    def __init__(self, value):
        if not isinstance(value, bool):
            raise PdfValueError
        self.value = value


class PdfNumeric(PdfObject):

    def __init__(self, value):
        if not (isinstance(value, int) or isinstance(value, float)):
            raise PdfValueError
        self.value = value


class PdfIndirectObject:

    def __init__(self):
        self.object_number = None
        self.generation_number = None
        self.attached = False
        self.free = False
        self.next_free_object = None

    def attach(self, object_number, generation_number):
        # attached means the object has been associated to the pdf body
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True

    def release(self, next_free_object):
        self.next_free_object = next_free_object
        self.free = True

    def format(self):
        if self.attached is False:
            raise Exception
        output_lines = [
            f"{self.object_number} {self.generation_number} obj"
        ]
        if self.obj_datatype == 'dictionary':
            pdf_dict = {
                '/Type': self.obj_type
            }
            pdf_dict.update(self.to_dict())
            output_lines.append(format_type(pdf_dict))
        elif self.obj_datatype == 'stream':
            stream_str = self.to_stream()
            output_lines.extend([
                format_type({'/Length': len(stream_str)}),
                'stream',
                stream_str,
                'endstream'
            ])

        output_lines.append("endobj")
        return '\n'.join(output_lines)+'\n'

    def format_ref(self):
        # TODO: maybe make ref a separate class
        if self.attached is False:
            raise Exception
        return f"{self.object_number} {self.generation_number} R"


class PdfFile:
    """
    The idea of the PdfFile is two-fold:
     1. provide an abstract representation of a pdf file as defined by the PDF spec
        (therefore effectively representing an abstract syntax tree)
     2. provide an interface for manipulating pdf files
    """

    def __init__(self, version=None):
        self.version, _ = get_optional_entry('version', version)

        self.header = None
        self.body = None
        self.cross_reference_table = None
        self.trailer = None

    def setup(self):
        # builds a basic structure
        self.header = FileHeader(self)
        self.body = FileBody(self)
        self.cross_reference_table = FileCrossReferenceTable(self)
        self.trailer = FileTrailer(self)

        self.body.setup()
        self.cross_reference_table.setup()
        return self

    def add_pdf_object(self, pdf_object):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.cross_reference_table.add_pdf_object(pdf_object)
        return pdf_object, entry

    def add_page(self):
        page = self.body.page_tree_root.add_page()
        self.add_pdf_object(page)
        return page

    def format(self):
        # convert the pdf file object into byte string
        output_lines = [
            self.header.format(),
            self.body.format(),
            self.cross_reference_table.format(),
            self.trailer.format(),
        ]
        return '\n'.join(output_lines)

    def write(self, io_buffer, linearized=False):
        # TODO: encoding must be handled specially based on the objects being used in PDF
        # the encoding will also determine how the cross-reference table and trailer get built
        # might want to make encoding/compressing/other-filtering a utility
        # possibly make linearized the default to optimize web read performance
        if not isinstance(io_buffer, io.IOBase):
            raise Exception
        if not io_buffer.writable():
            raise Exception
        io_buffer.write(self.format().encode('utf-8'))

    def read(self, io_buffer):
        # validate and parse an io buffer
        if not isinstance(io_buffer, io.IOBase):
            raise Exception
        if not io_buffer.seekable():
            raise Exception
        return self.parse(io_buffer)

    def parse(self, io_buffer):
        # merge an io buffer into a pdf file object
        # TODO: maybe add page numbers kwarg, since the random access pdf structure
        # enables us to load pages independently;
        # also consder adding merging behavior if the file object is already built
        self.header = FileHeader(self)
        self.body = FileBody(self)
        self.cross_reference_table = FileCrossReferenceTable(self)
        self.trailer = FileTrailer(self)

        self.header.parse(io_buffer)
        self.body.parse(io_buffer)
        self.cross_reference_table.parse(io_buffer)
        self.trailer.parse(io_buffer)
        return self


class FileBody:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.objects = {}
        self.fonts = {}

        self.zeroth_object = None
        self.free_object_list_tail = None
        self.page_tree_root = None
        self.document_catalog = None

        # set at format/parse-time
        self.object_byte_offset_map = None

        # set at format-time
        self.formatted = None

    def setup(self):
        # pdf file is being built from scratch, so create the basic objects

        # the zeroth object
        self.zeroth_object = PdfIndirectObject()
        self.add_pdf_object(self.zeroth_object)
        self.zeroth_object.release(self.zeroth_object)
        self.free_object_list_tail = self.zeroth_object

        # the document catalog object
        self.page_tree_root = PageTreeNode(self.pdf_file)
        self.document_catalog = DocumentCatalog(self.page_tree_root)
        self.add_pdf_object(self.document_catalog)
        self.add_pdf_object(self.page_tree_root)
        return self

    def add_pdf_object(self, pdf_object):
        if pdf_object.attached is True:
            object_number = pdf_object.object_number
            generation_number = pdf_object.generation_number
            if object_number is None:
                raise Exception
            if generation_number is None:
                raise Exception
            if generation_number == 65535:
                raise Exception
            generation_number += 1
        else:
            if len(self.objects) > 0:
                max_object_number, _ = sorted(self.objects)[-1]
                object_number = max_object_number + 1
                generation_number = 0
            else:
                # the zeroth object
                object_number = 0
                generation_number = 65535
        self.objects[(object_number, generation_number)] = pdf_object
        if isinstance(pdf_object, Font):
            self.fonts[pdf_object.font_name] = pdf_object
        pdf_object.attach(object_number, generation_number)
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
        byte_offset = len(self.pdf_file.header.formatted)+1
        output_lines = []
        object_byte_offset_map = {}
        for k in sorted(self.objects):
            pdf_object = self.objects[k]
            if pdf_object.free is False:
                formatted_object = pdf_object.format()
                object_byte_offset_map[(pdf_object.object_number, pdf_object.generation_number)] = byte_offset
                byte_offset += len(formatted_object)+1
                output_lines.append(formatted_object)
        self.object_byte_offset_map = object_byte_offset_map
        self.formatted = '\n'.join(output_lines)
        return self.formatted

    def parse(self, io_buffer):
        return self


class FileHeader:

    starter = '%PDF-'
    binary_indicator = '%âãÏÓ'
    
    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

        # set at format-time
        self.formatted = None

    def format(self):
        output_lines = [
            f"{self.starter}{self.pdf_file.version}",
            self.binary_indicator
        ]
        self.formatted = '\n'.join(output_lines)
        return self.formatted

    def parse(self, io_buffer):
        io_buffer.seek(0, io.SEEK_SET)
        lines = read_lines(io_buffer)
        first_line = next(lines, None)
        if first_line is None:
            raise Exception
        first_line = first_line.decode()
        if first_line.startswith(self.starter) is False:
            raise Exception
        self.pdf_file.version = first_line[self.starter:]
        return self


class FileCrossReferenceTable:

    starter = 'xref'

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.subsections = None
        self.current_crt_subsection_index = None

    def setup(self):
        # pdf file is being built from scratch, so create the basic objects
        self.subsections = [CrtSubsection(self.pdf_file)]
        self.current_crt_subsection_index = 0
        self.add_pdf_object(self.pdf_file.body.zeroth_object)
        self.add_pdf_object(self.pdf_file.body.document_catalog)
        self.add_pdf_object(self.pdf_file.body.page_tree_root)

    def add_pdf_object(self, pdf_object):
        subsection = self.subsections[self.current_crt_subsection_index]
        entry = CrtEntry(self.pdf_file, pdf_object)
        subsection.entries.append(entry)
        self.pdf_file.trailer.size += 1
        return entry

    def format(self):
        output_lines = [self.starter]
        output_lines.extend([subsection.format() for subsection in self.subsections])
        return '\n'.join(output_lines)

    def parse(self, io_buffer, xref_offset, num_objects):
        io_buffer.seek(xref_offset, io.SEEK_SET)
        lines = read_lines(io_buffer)
        count = 0
        first_line = next(lines, None)
        if first_line is None or first_line.decode() != self.starter:
            raise Exception
        while count < num_objects:
            subsection = CrtSubsection(self.pdf_file)
            self.subsections = self.subsections or []
            self.subsections.append(subsection)
            subsection.parse(io_buffer)

            count += len(subsection.entries)
        return self


class CrtSubsection:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.entries = []

    def format(self):
        if len(self.entries) == 0:
            raise Exception
        first_object_number = self.entries[0].pdf_object.object_number
        output_lines = [f"{first_object_number} {len(self.entries)}\n"]
        output_lines.extend([entry.format() for entry in self.entries])
        return ''.join(output_lines)

    def parse(self, io_buffer):
        return self


class CrtEntry:

    def __init__(self, pdf_file, pdf_object):
        self.pdf_file = pdf_file
        self.pdf_object = pdf_object

    def format(self):
        if self.pdf_object.free is True:
            first_item = self.pdf_object.next_free_object.object_number
            generation_number = self.pdf_object.generation_number
            if generation_number != 65535:
                # next generation number should this object be used again
                generation_number += 1
        else:
            object_key = (self.pdf_object.object_number, self.pdf_object.generation_number)
            first_item = self.pdf_file.body.object_byte_offset_map[object_key]
            generation_number = self.pdf_object.generation_number
        return f"{first_item:010} {generation_number:05} {'f' if self.pdf_object.free is True else 'n'} \n"

    def parse(self, io_buffer):
        return self


class FileTrailer:

    eof = '%%EOF'
    startxref = 'startxref'
    trailer = 'trailer'

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.size = 0

        # set at parse-time
        self.trailer_dictionary = None

    def format(self):
        output_lines = ['trailer']
        pdf_dict = {
            '/Root': self.pdf_file.body.document_catalog.format_ref(),
            '/Size': self.size
        }
        output_lines.extend([
            format_type(pdf_dict),
            self.startxref,
            str(len(self.pdf_file.header.formatted)+len(self.pdf_file.body.formatted)+2),
            self.eof
        ])
        return '\n'.join(output_lines)

    def parse(self, io_buffer):
        io_buffer.seek(0, io.SEEK_END)
        lines = reverse_read_lines(io_buffer)
        last_line = next(lines, None)
        if last_line is None or last_line.decode() != self.eof:
            raise Exception
        xref_offset, startxref = next(lines, None), next(lines, None)
        if xref_offset is None or startxref is None or startxref.decode() != self.startxref:
            raise Exception
        self.xref_offset = int(xref_offset)

        while True:
            # find start of trailer dictionary
            next_line = next(lines, None)
            if next_line is None:
                raise Exception
            next_line = next_line.decode()
            if next_line == self.trailer:
                start_offset = io_buffer.tell()
                break
        self.trailer_dictionary = parse_dict(io_buffer, start_offset)
        return self


class DocumentCatalog(PdfIndirectObject):

    obj_datatype = 'dictionary'
    obj_type = '/Catalog'

    def __init__(self,
            page_tree,
            version=None,
            # page_label_tree=None,
            page_layout=None,
            # outline_hierarchy=None,
            # article_threads=None,
            # named_destinations=None,
            # interactive_form=None,
            ):
        super().__init__()
        self.page_tree = page_tree

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

    def to_dict(self):
        pdf_dict = {
            '/Pages': self.page_tree.format_ref()
        }
        return pdf_dict


class PageTreeNode(PdfIndirectObject):

    obj_datatype = 'dictionary'
    obj_type = '/Pages'

    def __init__(self, pdf_file, parent=None):
        super().__init__()
        self.pdf_file = pdf_file
        self.parent = parent
        self.kids = []
        self.count = 0

        # inheritable properties
        self.resources = {'/Font': {}}
        self.media_box, _ = get_optional_entry('media_box', None)

    def to_dict(self):
        pdf_dict = {
            '/Kids': format_type([k.format_ref() for k in self.kids]),
            '/Count': self.count
        }
        if self.parent is not None:
            pdf_dict['/Parent'] = self.parent.format_ref()
        return pdf_dict

    def add_page(self):
        page = PageObject(self.pdf_file, self)
        self.kids.append(page)
        self.count += 1
        return page


class PageObject(PdfIndirectObject):

    obj_datatype = 'dictionary'
    obj_type = '/Page'

    def __init__(self, pdf_file, parent, resources=None, media_box=None, contents=None):
        super().__init__()
        self.pdf_file = pdf_file
        self.parent = parent

        self.font_mapping = {}
        self.resources = resources
        self.media_box = media_box
        self.contents = contents or []
        self.get_inherited()

    def get_inherited(self):
        self.resources = get_inherited_entry('resources', self, required=True)
        self.media_box = get_inherited_entry('media_box', self, required=True)

    def add_font(self, font_name):
        font_name, font_settings = get_optional_entry('font', font_name)
        if font_name not in self.pdf_file.body.fonts:
            font = Font(font_name, font_settings['sub_type'])
            self.pdf_file.add_pdf_object(font)
        else:
            font = self.pdf_file.body.fonts[font_name]
        font_number = max(self.font_mapping)+1 if len(self.font_mapping) > 0 else 1
        self.font_mapping[font_number] = font
        font_alias_name = f'/F{font_number}'
        self.resources['/Font'][font_alias_name] = font.format_ref()
        return font_alias_name

    def add_content_stream(self, content_objs):
        stream = ContentStream(content_objs)
        self.pdf_file.add_pdf_object(stream)
        self.contents.append(stream)
        return stream

    def add_text(self, text, font_name=None, size=None, line_size=None,
            translate_x=None, translate_y=None, scale_x=None, scale_y=None,
            skew_angle_a=None, skew_angle_b=None, rotation_angle=None):
        font_alias_name = self.add_font(font_name)
        text_obj = StreamTextObject(text, font_alias_name, size=size, line_size=line_size)
        gsm = StreamGraphicsState([text_obj])
        if translate_x is not None or translate_y is not None:
            gsm.add_translation(x=translate_x, y=translate_y)
        if rotation_angle is not None:
            gsm.add_rotation(rotation_angle)
        if scale_x is not None or scale_y is not None:
            gsm.add_scaling(x=scale_x, y=scale_y)
        if skew_angle_a is not None or skew_angle_b is not None:
            gsm.add_skew(angle_a=skew_angle_a, angle_b=skew_angle_b)
        self.add_content_stream([gsm])
        return text_obj

    def to_dict(self):
        pdf_dict = {
            '/Parent': self.parent.format_ref(),
            '/Resources': format_type(self.resources),
            '/MediaBox': format_type(self.media_box),
        }
        if len(self.contents) > 0:
            pdf_dict['/Contents'] = format_type([content.format_ref() for content in self.contents])
        return pdf_dict


class Font(PdfIndirectObject):

    obj_datatype = 'dictionary'
    obj_type = '/Font'

    def __init__(self, font_name, sub_type):
        super().__init__()
        self.font_name = font_name
        self.sub_type = sub_type

    def to_dict(self):
        pdf_dict = {
            '/Subtype': f"/{self.sub_type}",
            '/BaseFont': f"/{self.font_name}",
        }
        return pdf_dict


class ContentStream(PdfIndirectObject, PdfStream):

    obj_datatype = 'stream'

    def __init__(self, content_objs):
        super().__init__()
        self.content_objs = content_objs

    def to_stream(self):
        return '\n'.join([o.format() for o in self.content_objs])


class StreamGraphicsState:

    def __init__(self, content_objs, transformation_matrix=None):
        self.content_objs = content_objs
        self.transformation_matrix = transformation_matrix or \
            [[1, 0, 0],
             [0, 1, 0],
             [0, 0, 1]]

    def add_translation(self, x=None, y=None):
        self.transformation_matrix = np.matmul(
            [[1, 0, 0],
             [0, 1, 0],
             [x or 0, y or 0, 1]],
            self.transformation_matrix)

    def add_scaling(self, x=None, y=None):
        self.transformation_matrix = np.matmul(
            [[x or 1, 0, 0],
             [0, y or 1, 0],
             [0, 0, 1]],
            self.transformation_matrix)

    def add_skew(self, angle_a=None, angle_b=None):
        angle_a_tan = math.tan((angle_a or 0)*math.pi/180)
        angle_b_tan = math.tan((angle_b or 0)*math.pi/180)
        self.transformation_matrix = np.matmul(
            [[1, angle_a_tan, 0],
             [angle_b_tan, 1, 0],
             [0, 0, 1]],
            self.transformation_matrix)

    def add_rotation(self, angle=None):
        angle_cos = math.cos((angle or 0)*math.pi/180)
        angle_sin = math.sin((angle or 0)*math.pi/180)
        self.transformation_matrix = np.matmul(
            [[angle_cos, angle_sin, 0],
             [-angle_sin, angle_cos, 0],
             [0, 0, 1]],
            self.transformation_matrix)

    def format(self):
        if len(self.content_objs) == 0:
            raise Exception
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        output_lines = [
            "q",
            f"{' '.join(map(str, [a, b, c, d, e, f]))} cm",
            *[o.format() for o in self.content_objs],
            "Q"
        ]
        return '\n'.join(output_lines)


class StreamTextObject:

    def __init__(self, text, font,
            size=None, line_size=None, transformation_matrix=None):
        self.text = text
        self.font = font
        self.size = size or 12
        self.line_size = line_size or 14.4
        self.transformation_matrix = transformation_matrix or \
            [[1, 0, 0],
             [0, 1, 0],
             [0, 0, 1]]
        self.color_matrix = None

    def format(self):
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        output_lines = [
            "BT ",
            f"{' '.join(map(str, [a, b, c, d, e, f]))} Tm",
            f"{self.font} {self.size} Tf",
            f"{self.line_size} TL",
            f"({self.text}) Tj",
            "T*",
            "ET"
        ]
        return '\n'.join(output_lines)
