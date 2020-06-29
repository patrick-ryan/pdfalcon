import io
import math
import numpy as np
import textwrap


OPTIONS = {
    'version': {
        'default': 1.4,
        'options': {1.4: {}, 1.5: {}, 1.6: {}, 1.7: {}}
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


class PdfIoError(Exception):
    pass


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


def parse_type(io_buffer):
    # parser for the basic delimited types, maintains buffer position
    # 
    # types: Boolean values, Integer and Real numbers, Strings, Names, Arrays,
    #   Dictionaries, Streams, and the null object
    tokens = read_pdf_tokens(io_buffer)
    start_offset = io_buffer.tell()
    first_token = next(tokens, None)
    if first_token is None:
        # unexpected EOF
        raise PdfParseError
    elif first_token == b'<':
        next_token = next(tokens, None)
        if next_token == b'<':
            # dictionary type
            io_buffer.seek(start_offset, io.SEEK_SET)
            result = PdfDict().parse(io_buffer)

            dict_end_offset = io_buffer.tell()
            stream_tokens = read_pdf_tokens(io_buffer)
            dict_post_token = next(stream_tokens, None)

            if dict_post_token == b'stream':
                # TODO: split into new parse method
                # stream type
                stream_contents = []
                while True:
                    stream_token = next(stream_tokens, None)
                    if stream_token == b'endstream':
                        break
                    # TODO: parse stream objects, determine if ContentStream,
                    #   apply decoding filters, determine handling of special whitespace

                return PdfStream(result, stream_contents)
            else:
                io_buffer.seek(dict_end_offset, io.SEEK_SET)
                return result
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
            try:
                # validate hexadecimal input
                int(hex_string, 16)
            except ValueError:
                raise PdfParseError
            return PdfHexString(hex_string.decode())
    elif first_token == b']':
        return first_token
    elif first_token == b'[':
        # array type
        result = []
        while True:
            item = parse_type(io_buffer)
            if isinstance(item, PdfComment):
                continue
            if item == b']':
                break
            result.append(item)
        return PdfArray(result)
    elif first_token == b'}':
        return first_token
    elif first_token == b'{':
        # TODO: function expression type
        raise PdfParseError
    elif first_token == b'(':
        # string literal type
        literal_string = b''
        stack_level = 0
        while True:
            next_char = io_buffer.read(1)
            if next_char == b'(':
                stack_level += 1
            elif next_char == b')':
                if stack_level == 0:
                    break
                stack_level -= 1
            literal_string += next_char
        return PdfLiteralString(literal_string.decode())
    elif first_token == b'true':
        # boolean type
        return True
    elif first_token == b'false':
        # boolean type
        return False
    elif first_token == b'null':
        # null type
        return None
    elif first_token == b'%':
        # comment type
        comment_line = next(read_lines(io_buffer))
        return PdfComment(comment_line.decode())
    elif first_token == b'/':
        # name type
        solidus_end_offset = io_buffer.tell()
        name = next(tokens, None)
        name_end_offset = io_buffer.tell()
        if solidus_end_offset != name_end_offset-len(name):
            # no whitespace allowed between solidus and name
            raise PdfParseError
        return PdfName(name.decode())
    else:
        try:
            # numeric type
            float(first_token)
        except ValueError:
            # unrecognized type
            raise PdfParseError
        token_end_offset = io_buffer.tell()
        next_token = next(tokens, None)
        try:
            int(next_token)
        except ValueError:
            io_buffer.seek(token_end_offset, io.SEEK_SET)
            return float(first_token)
        final_token = next(tokens, None)
        if final_token == b'R':
            return PdfIndirectObjectRef(int(first_token), int(next_token))
        else:
            io_buffer.seek(token_end_offset, io.SEEK_SET)
            return float(first_token)


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
                io_buffer.seek(token_end_offset, io.SEEK_CUR)
                if cur_token != b'':
                    # end of token
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
                    token_end_offset = 1
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

        io_buffer.seek(-len(line_remainder), io.SEEK_CUR)

    yield line_remainder.strip()


class PdfObject:

    def format(self):
        raise NotImplementedError


class PdfHexString(PdfObject, str):

    _str = '<{contents}>'

    def format(self):
        return self._str.format(contents=self)


class PdfLiteralString(PdfObject, str):

    _str = '({contents})'

    def format(self):
        return self._str.format(contents=self)


class PdfDict(PdfObject, dict):

    _str = textwrap.dedent('''
        <<
          {contents}
        >>
    ''').strip()

    def format(self):
        fmt = lambda x: x.format() if isinstance(x, PdfObject) else str(x)
        contents = '\n'.join([f"{fmt(k)} {fmt(v)}" for k,v in self.items()])
        return self._str.format(contents=contents)

    def parse(self, io_buffer):
        tokens = read_pdf_tokens(io_buffer)
        first_token = next(tokens, None)
        second_token = next(tokens, None)
        if first_token != b'<' or second_token != b'<':
            raise PdfParseError
        current_key = None
        while True:
            cur_offset = io_buffer.tell()
            tokens = read_pdf_tokens(io_buffer)
            if next(tokens, None) == b'>':
                # end of dict
                if next(tokens, None) != b'>':
                    raise PdfParseError
                break
            else:
                # not end, back up cursor
                io_buffer.seek(cur_offset, io.SEEK_SET)
            if current_key is None:
                key = parse_type(io_buffer)
                if isinstance(key, PdfComment):
                    continue
                current_key = key
            else:
                value = parse_type(io_buffer)
                if isinstance(value, PdfComment):
                    continue
                self[current_key] = value
                current_key = None
        return self


class PdfStream(PdfObject):

    _str = textwrap.dedent('''
        {stream_dict}
        stream
        {contents}
        endstream
    ''').strip()

    def __init__(self, contents):
        self.contents = contents

    def format(self):
        contents = '\n'.join([x.format() for x in self.contents])
        stream_dict = PdfDict({PdfName('Length'): len(contents)}).format()
        return self._str.format(stream_dict=stream_dict, contents=contents)


class PdfArray(PdfObject, list):

    _str = '[ {contents} ]'

    def format(self):
        fmt = lambda x: x.format() if isinstance(x, PdfObject) else str(x)
        contents = ' '.join(map(fmt, self))
        return self._str.format(contents=contents)


class PdfName(PdfObject, str):

    _str = '/{contents}'

    def format(self):
        return self._str.format(contents=self)


class PdfComment(PdfObject, str):

    _str = '%{contents}'

    def format(self):
        return self._str.format(contents=self)


class PdfIndirectObject:

    _str = textwrap.dedent('''
        {object_number} {generation_number} obj
        {contents}
        endobj
    ''').strip()

    def __init__(self):
        self.object_number = None
        self.generation_number = None
        self.attached = False
        self.free = False
        self.next_free_object = None
        self.ref = None

    def attach(self, object_number, generation_number):
        # attached means the object has been given identifying information
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True
        self.ref = PdfIndirectObjectRef(object_number, generation_number)

    def release(self, next_free_object):
        self.next_free_object = next_free_object
        self.free = True

    def format(self, contents):
        if self.attached is False:
            raise PdfFormatError
        return self._str.format(
            object_number=self.object_number,
            generation_number=self.generation_number,
            contents=contents.format()
        )


class PdfIndirectObjectRef(PdfObject):

    _str = '{object_number} {generation_number} R'

    def __init__(self, object_number, generation_number):
        if not (isinstance(object_number, int) or isinstance(object_number, float)):
            raise PdfValueError
        if not (isinstance(generation_number, int) or isinstance(generation_number, float)):
            raise PdfValueError
        self.object_number = object_number
        self.generation_number = generation_number

    def format(self):
        return self._str.format(
            object_number=self.object_number,
            generation_number=self.generation_number
        )


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

        # ordered list of original section and update sections in the file;
        #   original is at index 0, most recent update is at index -1
        self.sections = []

        # set at format-time
        self.cur_format_byte_offset = None

    def setup(self):
        # builds a basic structure
        self.header = FileHeader(self)
        self.sections = [FileSection(self).setup()]
        return self

    def format(self):
        # convert the pdf file object into byte string
        if len(self.sections) == 0:
            raise PdfBuildError

        output_lines = []
        formatted_header = self.header.format()
        self.cur_format_byte_offset = len(formatted_header.encode())+1
        output_lines.append(formatted_header)

        for section in self.sections:
            formatted_section = section.format()
            self.cur_format_byte_offset += 1
            output_lines.append(formatted_section)

        return '\n'.join(output_lines)

    def parse(self, io_buffer):
        # merge an io buffer into a pdf file object
        self.header = FileHeader(self)
        self.header.parse(io_buffer)

        while True:
            if len(self.sections) > 0 and self.sections[0].trailer.trailer_dictionary.get('Prev') is None:
                break
            file_section = FileSection(self).parse(io_buffer)
            self.sections = [file_section] + self.sections

        # if trailer_size != actual_size:
        #     # metadata inconsistency
        #     raise PdfParseError

        return self

    def add_update(self):
        if len(self.sections) == 0:
            raise PdfBuildError
        file_update = FileSection(self)
        self.sections.append(file_update)
        return file_update

    def add_page(self):
        if len(self.sections) == 0:
            raise PdfBuildError
        page = self.sections[-1].body.page_tree_root.add_page()
        self.sections[-1].add_pdf_object(page)
        return page

    def write(self, io_buffer, linearized=False):
        # write pdf file encoded string to the supplied io buffer
        # 
        # TODO: encoding must be handled specially based on the objects being used in PDF
        # the encoding will also determine how the cross-reference table and trailer get built
        # might want to make encoding/compressing/other-filtering a utility
        # possibly make linearized the default to optimize web read performance
        if not isinstance(io_buffer, io.IOBase):
            raise PdfIoError
        if not io_buffer.writable():
            raise PdfIoError
        io_buffer.write(self.format().encode('utf-8'))

    def read(self, io_buffer):
        # validate and parse an io buffer
        # 
        # TODO: maybe add page numbers kwarg, since the random access pdf structure
        # enables us to load pages independently;
        # also consder adding merging behavior if the file object is already built
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
        return self._str.format(version=self.pdf_file.version)

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
        self.pdf_file.version = float(first_line[len(_pdf):])
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
        self.pdf_file.cur_format_byte_offset += len(formatted_body)+1
        self.trailer.crt_byte_offset = self.pdf_file.cur_format_byte_offset

        formatted_crt_section = self.crt_section.format()
        self.pdf_file.cur_format_byte_offset += len(formatted_crt_section)+1

        formatted_trailer = self.trailer.format()
        self.pdf_file.cur_format_byte_offset += len(formatted_trailer)+1

        return '\n'.join([formatted_body, formatted_crt_section, formatted_trailer])

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
            prev_crt_byte_offset = self.pdf_file.sections[0].trailer.trailer_dictionary['Prev']
            io_buffer.seek(prev_crt_byte_offset, io.SEEK_SET)
            self.crt_section.parse(io_buffer)
            self.trailer.parse(io_buffer)
            self.body.parse(io_buffer)

        return self

    def add_pdf_object(self, pdf_object):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.crt_section.add_pdf_object(pdf_object)
        return pdf_object, entry


class FileBody:

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.objects = {}
        self.fonts = {}

        self.zeroth_object = None
        self.free_object_list_tail = None
        self.page_tree_root = None
        self.document_catalog = None

        # set at format-time
        self.object_byte_offset_map = None

    def setup(self):
        # pdf file is being built from scratch, so create the basic objects
        self.zeroth_object = PdfIndirectObject()
        self.add_pdf_object(self.zeroth_object)
        self.zeroth_object.release(self.zeroth_object)
        self.free_object_list_tail = self.zeroth_object

        self.page_tree_root = PageTreeNode(self.pdf_section)
        self.document_catalog = DocumentCatalog(self.page_tree_root)
        self.add_pdf_object(self.document_catalog)
        self.add_pdf_object(self.page_tree_root)
        return self

    def add_pdf_object(self, pdf_object):
        if pdf_object.attached is True:
            object_number = pdf_object.object_number
            generation_number = pdf_object.generation_number
            if object_number is None:
                raise PdfBuildError
            if generation_number is None:
                raise PdfBuildError
            if generation_number == 65535:
                raise PdfBuildError
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
        byte_offset = self.pdf_section.pdf_file.cur_format_byte_offset
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
        return '\n'.join(output_lines)

    def parse(self, io_buffer):
        # parse objects supplied by cross-reference table
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
        self.add_pdf_object(self.pdf_section.body.document_catalog)
        self.add_pdf_object(self.pdf_section.body.page_tree_root)

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
        output_lines = [f"{first_object_number} {len(self.entries)}\n"]
        output_lines.extend([entry.format() for entry in self.entries])
        return ''.join(output_lines)

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
        return f"{first_item:010} {generation_number:05} {'f' if self.pdf_object.free is True else 'n'} \n"

    def parse(self, io_buffer):
        line = next(read_lines(io_buffer), None)
        if line is None:
            raise PdfParseError
        first_item, generation_number, usage_symbol = line.split()
        self.first_item = int(first_item)
        self.generation_number = int(generation_number)
        self.free = True if usage_symbol == 'f' else False
        return self


class FileTrailer:

    _str = textwrap.dedent('''
        trailer
        {trailer_dictionary}
        startxref
        {crt_byte_offset}
        %%EOF
    ''').strip()
    _tokens = _str.encode('utf-8').split()

    def __init__(self, pdf_section):
        self.pdf_section = pdf_section
        self.size = 0

        # set at format/parse-time
        self.crt_byte_offset = None

        # set at parse-time
        self.trailer_dictionary = None

    def format(self):
        pdf_dict = PdfDict({
            PdfName('Root'): self.pdf_section.body.document_catalog.ref,
            PdfName('Size'): self.size,
        })
        trailer_dictionary = pdf_dict.format()
        return self._str.format(trailer_dictionary=trailer_dictionary, crt_byte_offset=self.crt_byte_offset)

    def parse(self, io_buffer):
        _trailer, _, _startxref, _, _eof = self._tokens
        next_token = next(read_pdf_tokens(io_buffer), None)
        if next_token != _trailer:
            raise PdfParseError

        self.trailer_dictionary = PdfDict().parse(io_buffer)
        if not isinstance(self.trailer_dictionary, PdfDict):
            raise PdfParseError

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


class DocumentCatalog(PdfIndirectObject):

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

    def format(self):
        contents = PdfDict({
            PdfName('Type'):  PdfName('Catalog'),
            PdfName('Pages'): self.page_tree.ref,
        })
        return super().format(contents)


class PageTreeNode(PdfIndirectObject):

    def __init__(self, pdf_section, parent=None):
        super().__init__()
        self.pdf_section = pdf_section
        self.parent = parent
        self.kids = PdfArray()
        self.count = 0

        # inheritable properties
        self.resources = PdfDict({PdfName('Font'): PdfDict()})
        media_box, _ = get_optional_entry('media_box', None)
        self.media_box = PdfArray(media_box)

    def format(self):
        contents = PdfDict({
            PdfName('Type'):  PdfName('Pages'),
            PdfName('Kids'): self.kids,
            PdfName('Count'): self.count,
        })
        if self.parent is not None:
            pdf_dict[PdfName('Parent')] = self.parent.ref
        return super().format(contents)

    def add_page(self):
        page = PageObject(self.pdf_section, self)
        self.kids.append(page)
        self.count += 1
        return page


class PageObject(PdfIndirectObject):

    def __init__(self, pdf_section, parent, resources=None, media_box=None, contents=None):
        super().__init__()
        self.pdf_section = pdf_section
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
        sub_type = PdfName(font_settings['sub_type'])
        font_name = PdfName(font_name)
        if font_name not in self.pdf_section.body.fonts:
            font = Font(font_name, sub_type)
            self.pdf_section.add_pdf_object(font)
        else:
            font = self.pdf_section.body.fonts[font_name]
        font_number = max(self.font_mapping)+1 if len(self.font_mapping) > 0 else 1
        self.font_mapping[font_number] = font
        font_alias_name = PdfName(f'F{font_number}')
        self.resources['Font'][font_alias_name] = font.ref
        return font_alias_name

    def add_content_stream(self, contents):
        stream = ContentStream(contents)
        self.pdf_section.add_pdf_object(stream)
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

    def format(self):
        contents = PdfDict({
            PdfName('Type'):  PdfName('Page'),
            PdfName('Parent'): self.parent.ref,
            PdfName('Resources'): self.resources,
            PdfName('MediaBox'): self.media_box,
        })
        if len(self.contents) > 0:
            contents[PdfName('Contents')] = self.contents
        return super().format(contents)


class Font(PdfIndirectObject):

    def __init__(self, font_name, sub_type):
        super().__init__()
        self.font_name = font_name
        self.sub_type = sub_type

    def format(self):
        contents = PdfDict({
            PdfName('Type'):  PdfName('Font'),
            PdfName('Subtype'): self.sub_type,
            PdfName('BaseFont'): self.font_name,
        })
        return super().format(contents)


class ContentStream(PdfIndirectObject):

    def __init__(self, contents):
        super().__init__()
        self.contents = contents

    def format(self):
        contents = PdfStream(self.contents)
        return super().format(contents)


class StreamGraphicsState:

    def __init__(self, contents, transformation_matrix=None):
        self.contents = contents
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
        if len(self.contents) == 0:
            raise PdfFormatError
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        output_lines = [
            "q",
            f"{' '.join(map(str, [a, b, c, d, e, f]))} cm",
            *[o.format() for o in self.contents],
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
            f"{self.font.format()} {self.size} Tf",
            f"{self.line_size} TL",
            f"({self.text}) Tj",
            "T*",
            "ET"
        ]
        return '\n'.join(output_lines)
