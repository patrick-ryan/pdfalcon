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


def parse_pdf_object(io_buffer):
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
                # stream type
                return PdfStream(stream_dict=result).parse(io_buffer)
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
            item = parse_pdf_object(io_buffer)
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
        return PdfBoolean(value=True)
    elif first_token == b'false':
        # boolean type
        return PdfBoolean(value=False)
    elif first_token == b'null':
        # null type
        return PdfNull()
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
            int(first_token)
        except ValueError:
            try:
                return PdfReal(first_token)
            except ValueError:
                # unrecognized type
                raise PdfParseError
        token_end_offset = io_buffer.tell()
        next_token = next(tokens, None)
        try:
            int(next_token)
        except ValueError:
            io_buffer.seek(token_end_offset, io.SEEK_SET)
            return PdfInteger(first_token)
        final_token = next(tokens, None)
        if final_token == b'R':
            return PdfIndirectObjectRef(int(first_token), int(next_token))
        else:
            io_buffer.seek(token_end_offset, io.SEEK_SET)
            return PdfInteger(first_token)


class PdfObject:

    def format(self):
        raise NotImplementedError

    def parse(self, io_buffer):
        raise NotImplementedError


class PdfBoolean(PdfObject):

    def __init__(self, value=None):
        if value is not None and not isinstance(value, bool):
            raise PdfValueError
        self.value = value

    def format(self):
        return 'true' if self.value is True else 'false'


class PdfNull(PdfObject):

    def format(self):
        return 'null'


class PdfNumeric(PdfObject):

    def format(self):
        return str(self.value)


class PdfInteger(PdfNumeric):

    def __init__(self, value=None):
        self.value = int(value or 0)


class PdfReal(PdfNumeric):

    def __init__(self, value=None):
        self.value = float(value or 0)


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
                key = parse_pdf_object(io_buffer)
                if isinstance(key, PdfComment):
                    continue
                current_key = key
            else:
                value = parse_pdf_object(io_buffer)
                if isinstance(value, PdfComment):
                    continue
                self[current_key] = value
                current_key = None
        return self


def parse_stream_object(io_buffer, _op_args=None):
    _op_args = _op_args or []
    tokens = read_pdf_tokens(io_buffer)
    start_offset = io_buffer.tell()
    first_token = next(tokens, None)
    if first_token is None:
        # unexpected EOF
        raise PdfParseError
    elif first_token == b'endstream':
        if len(_op_args) > 0:
            # unexpected end of instruction
            raise PdfParseError
        return None
    elif first_token == b'q':
        return StateSaveOperation()
    elif first_token == b'Q':
        return StateRestoreOperation()
    elif first_token == b'cm':
        if len(_op_args) != 6:
            raise PdfParseError
        a, b, c, d, e, f = _op_args
        transformation_matrix = \
            [[a, b, 0],
             [c, d, 0],
             [e, f, 1]]
        return ConcatenateMatrixOperation(transformation_matrix=transformation_matrix)
    elif first_token == b'BT':
        io_buffer.seek(start_offset, io.SEEK_SET)
        return StreamTextObject().parse(io_buffer)
    else:
        # must be an instruction arg
        io_buffer.seek(start_offset, io.SEEK_SET)
        _op_args.append(parse_pdf_object(io_buffer))
        return parse_stream_object(io_buffer, _op_args=_op_args)


class PdfStream(PdfObject):

    _str = textwrap.dedent('''
        {stream_dict}
        stream
        {contents}
        endstream
    ''').strip()

    def __init__(self, stream_dict=None, contents=None):
        self.stream_dict = stream_dict
        self.contents = contents or []

    def format(self):
        if self.contents is None:
            raise PdfFormatError
        contents = '\n'.join([x.format() for x in self.contents])
        stream_dict = PdfDict(**(self.stream_dict or {}), **{PdfName('Length'): len(contents)}).format()
        return self._str.format(stream_dict=stream_dict, contents=contents)

    def parse(self, io_buffer):
        # TODO: parse stream objects, determine if ContentStream,
        #   apply decoding filters, determine handling of special whitespace
        if self.stream_dict is None:
            self.stream_dict = PdfDict().parse(io_buffer)
        while True:
            parsed_object = parse_stream_object(io_buffer)
            if parsed_object is None:
                break
            self.contents.append(parsed_object)
        return self


class GraphicsObject:

    def format(self):
        raise NotImplementedError

    def parse(self, io_buffer):
        raise NotImplementedError


class GraphicsOperation:

    def format(self):
        raise NotImplementedError

    def parse(self, io_buffer):
        raise NotImplementedError


class StateSaveOperation(GraphicsOperation):

    def format(self):
        return 'q'


class StateRestoreOperation(GraphicsOperation):

    def format(self):
        return 'Q'


class ConcatenateMatrixOperation(GraphicsOperation):

    def __init__(self, transformation_matrix=None):
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
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        return f"{a} {b} {c} {d} {e} {f} cm"


class TextFontOperation(GraphicsOperation):

    def __init__(self, font_alias_name=None, size=None):
        self.font_alias_name = font_alias_name
        self.size = size

    def format(self):
        return f"{self.font_alias_name.format()} {self.size} Tf"


class TextLeadingOperation(GraphicsOperation):

    def __init__(self, leading):
        self.leading = leading

    def format(self):
        return f"{self.leading} TL"


class TextMatrixOperation(GraphicsOperation):

    def __init__(self, transformation_matrix=None):
        self.transformation_matrix = transformation_matrix or \
            [[1, 0, 0],
             [0, 1, 0],
             [0, 0, 1]]

    def format(self):
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        return f"{a} {b} {c} {d} {e} {f} Tm"


class TextNextLineOperation(GraphicsOperation):

    def format(self):
        return 'T*'


class TextShowOperation(GraphicsOperation):

    def __init__(self, text=None):
        self.text = text

    def format(self):
        return f"({self.text}) Tj"


class StreamTextObject(GraphicsObject):

    def __init__(self, contents=None):
        self.contents = contents or []

    def format(self):
        output_lines = [
            "BT ",
            *[c.format() for c in self.contents],
            "ET"
        ]
        return '\n'.join(output_lines)

    def parse(self, io_buffer):
        tokens = read_pdf_tokens(io_buffer)
        first_token = next(tokens, None)
        if first_token is None:
            # unexpect EOF
            raise PdfParseError
        _op_args = []
        while True:
            start_offset = io_buffer.tell()
            token = next(tokens, None)
            if token is None:
                # unexpect EOF
                raise PdfParseError
            elif token == b'ET':
                if len(_op_args) != 0:
                    raise PdfParseError
                break
            elif token == b'Tj':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextShowOperation(text=_op_args[0]))
                _op_args = []
            elif token == b'TL':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextLeadingOperation(leading=_op_args[0]))
                _op_args = []
            elif token == b'Tf':
                if len(_op_args) != 2:
                    raise PdfParseError
                self.contents.append(TextFontOperation(font_alias_name=_op_args[0], size=_op_args[1]))
                _op_args = []
            elif token == b'Tm':
                if len(_op_args) != 6:
                    raise PdfParseError
                a, b, c, d, e, f = _op_args
                transformation_matrix = \
                    [[a, b, 0],
                     [c, d, 0],
                     [e, f, 1]]
                self.contents.append(TextMatrixOperation(transformation_matrix=transformation_matrix))
                _op_args = []
            elif token == b'T*':
                if len(_op_args) != 0:
                    raise PdfParseError
                self.contents.append(TextNextLineOperation())
            else:
                io_buffer.seek(start_offset, io.SEEK_SET)
                _op_args.append(parse_pdf_object(io_buffer))
                tokens = read_pdf_tokens(io_buffer)
        return self


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


class PdfIndirectObject(PdfObject):

    _str = textwrap.dedent('''
        {object_number} {generation_number} obj
        {contents}
        endobj
    ''').strip()
    _tokens = _str.encode('utf-8').split()

    def __init__(self):
        self.object_number = None
        self.generation_number = None
        self.attached = False
        self.free = False
        self.next_free_object = None
        self.ref = None
        self.contents = None

    @property
    def object_key(self):
        return (self.object_number, self.generation_number)

    def attach(self, object_number, generation_number, contents):
        # attached means the object has been given identifying information
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True
        self.ref = PdfIndirectObjectRef(object_number, generation_number)
        self.contents = contents
        return self

    def release(self, next_free_object):
        self.next_free_object = next_free_object
        self.free = True

    def format(self):
        if self.attached is False:
            raise PdfFormatError
        return self._str.format(
            object_number=self.object_number,
            generation_number=self.generation_number,
            contents=self.contents.format()
        )

    def parse(self, io_buffer):
        _, _, _obj, _, _endobj = self._tokens
        line = next(read_lines(io_buffer), None)
        if line is None:
            raise PdfParseError
        line_parts = line.split()
        if len(line_parts) != 3 or line_parts[2] != b'obj':
            raise PdfParseError
        self.contents = parse_pdf_object(io_buffer)
        final_token = next(read_pdf_tokens(io_buffer), None)
        if final_token != _endobj:
            raise PdfParseError
        return self


class PdfIndirectObjectRef(PdfObject):

    _str = '{object_number} {generation_number} R'

    def __init__(self, object_number, generation_number):
        if not (isinstance(object_number, int) or isinstance(object_number, float)):
            raise PdfValueError
        if not (isinstance(generation_number, int) or isinstance(generation_number, float)):
            raise PdfValueError
        self.object_number = object_number
        self.generation_number = generation_number

    @property
    def object_key(self):
        return (self.object_number, self.generation_number)

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
        self.cur_format_byte_offset = len(formatted_header.encode())+1
        output_lines.append(formatted_header)

        for section in self.sections:
            formatted_section = section.format()
            self.cur_format_byte_offset += 1
            output_lines.append(formatted_section)

        return '\n'.join(output_lines)

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
                byte_offset += len(formatted_object)+1
                output_lines.append(formatted_object)
        self.object_byte_offset_map = object_byte_offset_map
        return '\n'.join(output_lines)

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
        self.pdf_section.trailer.size.value += 1
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
        self.media_box = PdfArray(media_box)
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
            self.count.value += 1
        return self

    def add_page(self):
        page = PageObject(self.pdf_file, self).setup()
        self.children.append(page)
        self.kids.append(page.pdf_object.ref)
        self.count.value += 1
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
