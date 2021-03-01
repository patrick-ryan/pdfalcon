import abc
import base64
import codecs
import collections
import dataclasses
import inspect
import io
import math
import numbers
import numpy as np
import typing
import zlib

from PIL import Image

from pdfalcon.exceptions import PdfFormatError, PdfParseError, PdfValueError
from pdfalcon.parsing import read_lines, read_pdf_tokens


PDF_DOC_ENCODING = {
    0x16: "\u0017",
    0x18: "\u02D8",
    0x19: "\u02C7",
    0x1A: "\u02C6",
    0x1B: "\u02D9",
    0x1C: "\u02DD",
    0x1D: "\u02DB",
    0x1E: "\u02DA",
    0x1F: "\u02DC",
    0x80: "\u2022",
    0x81: "\u2020",
    0x82: "\u2021",
    0x83: "\u2026",
    0x84: "\u2014",
    0x85: "\u2013",
    0x86: "\u0192",
    0x87: "\u2044",
    0x88: "\u2039",
    0x89: "\u203A",
    0x8A: "\u2212",
    0x8B: "\u2030",
    0x8C: "\u201E",
    0x8D: "\u201C",
    0x8E: "\u201D",
    0x8F: "\u2018",
    0x90: "\u2019",
    0x91: "\u201A",
    0x92: "\u2122",
    0x93: "\uFB01",
    0x94: "\uFB02",
    0x95: "\u0141",
    0x96: "\u0152",
    0x97: "\u0160",
    0x98: "\u0178",
    0x99: "\u017D",
    0x9A: "\u0131",
    0x9B: "\u0142",
    0x9C: "\u0153",
    0x9D: "\u0161",
    0x9E: "\u017E",
    0xA0: "\u20AC",
}

ALLOWED_NAME_CHARS = set(range(33, 127)) - {ord(c) for c in "#%/()<>[]{}"}


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
                next(read_lines(io_buffer))
                return PdfStream(stream_dict=result).parse(io_buffer, skip_stream_dict=True)
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

        codec_length = len(codecs.BOM_UTF16_BE)
        if literal_string[:codec_length] == codecs.BOM_UTF16_BE:
            literal_string = literal_string[codec_length:].decode('utf_16_be')
        else:
            formatter = lambda b: PDF_DOC_ENCODING.get(b, chr(b))
            literal_string = ''.join(map(formatter, literal_string))

        return PdfLiteralString(literal_string)
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
        return PdfName(name.decode('us-ascii'))
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


class BaseObject(abc.ABC):

    @abc.abstractmethod
    def __bytes__(self):
        pass

    @classmethod
    def _clone_obj(cls, obj):
        if isinstance(obj, list):
            return [cls._clone_obj(x) for x in obj]
        elif isinstance(obj, dict):
            return {cls._clone_obj(k): cls._clone_obj(v) for k,v in obj.items()}
        elif hasattr(obj, 'clone'):
            return obj.clone()
        else:
            return obj

    def clone(self):
        new_obj = self.__class__()
        for k,v in vars(self).items():
            setattr(new_obj, k, self._clone_obj(v))
        return new_obj


class PdfObject(BaseObject):
    pass


class GraphicsObject(BaseObject):
    pass


class GraphicsOperation(BaseObject):
    pass


class PdfBoolean(PdfObject):

    def __init__(self, value=None):
        if value is not None and not isinstance(value, bool):
            raise PdfValueError
        self.value = value

    def __bytes__(self):
        return b'true' if self.value is True else b'false'


class PdfNull(PdfObject):

    def __bytes__(self):
        return b'null'


class PdfNumeric(numbers.Real, PdfObject):

    def __eq__(self, other):
        return self.value.__eq__(other)

    def __abs__(self):
        value = self.value.__abs__(other)
        return self.__class__(value)

    def __add__(self, other):
        value = self.value.__add__(other)
        return self.__class__(value)

    def __le__(self, other):
        return self.value.__le__(other)

    def __lt__(self, other):
        return self.value.__lt__(other)

    def __mod__(self, other):
        value = self.value.__mod__(other)
        return self.__class__(value)

    def __mul__(self, other):
        value = self.value.__mul__(other)
        return self.__class__(value)

    def __neg__(self):
        value = self.value.__neg__()
        return self.__class__(value)

    def __pos__(self):
        value = self.value.__pos__()
        return self.__class__(value)

    def __pow__(self, other):
        value = self.value.__pow__(other)
        return self.__class__(value)

    def __radd__(self, other):
        value = self.value.__radd__(other)
        return self.__class__(value)

    def __rmod__(self, other):
        value = self.value.__rmod__(other)
        return self.__class__(value)

    def __rmul__(self, other):
        value = self.value.__rmul__(other)
        return self.__class__(value)

    def __rpow__(self, other):
        value = self.value.__rpow__(other)
        return self.__class__(value)

    def __rtruediv__(self, other):
        value = self.value.__rtruediv__(other)
        return self.__class__(value)

    def __truediv__(self, other):
        value = self.value.__truediv__(other)
        return self.__class__(value)

    def __ceil__(self):
        value = self.value.__ceil__()
        return self.__class__(value)

    def __int__(self):
        return self.value.__int__()

    def __float__(self):
        return self.value.__float__()

    def __floor__(self):
        value = self.value.__floor__()
        return self.__class__(value)

    def __floordiv__(self, other):
        value = self.value.__floordiv__(other)
        return self.__class__(value)

    def __rfloordiv__(self, other):
        value = self.value.__rfloordiv__(other)
        return self.__class__(value)

    def __round__(self):
        value = self.value.__round__()
        return self.__class__(value)

    def __trunc__(self):
        value = self.value.__trunc__()
        return self.__class__(value)


@dataclasses.dataclass(eq=False)
class PdfInteger(PdfNumeric):

    value: numbers.Real

    def __init__(self, *args, **kwargs):
        self.value = int(*args, **kwargs)

    def __bytes__(self):
        return b'%d' % self.value


@dataclasses.dataclass(eq=False)
class PdfReal(PdfNumeric):

    value: numbers.Real

    def __init__(self, *args, **kwargs):
        self.value = float(*args, **kwargs)

    def __bytes__(self):
        return b'%f' % self.value


@dataclasses.dataclass
class PdfString(PdfObject):

    value: str

    def __init__(self, *args, **kwargs):
        self.value = str(*args, **kwargs)

    def __getitem__(self, index):
        return self.value.__getitem__(index)

    def __eq__(self, other):
        return self.value.__eq__(other)

    def __hash__(self):
        return self.value.__hash__()


class PdfHexString(PdfString):

    def __bytes__(self):
        formatter = lambda b: b"%02X" % b
        return b'<%b>' % b''.join(map(formatter, self.value))


class PdfLiteralString(PdfString):

    def __bytes__(self):
        return b'(%b)' % (self.value.encode('utf_16_be'))


@dataclasses.dataclass
class PdfDict(collections.abc.MutableMapping, PdfObject):

    def __init__(self, *args, **kwargs):
        self.value = dict(*args, **kwargs)

    def __bytes__(self):
        formatter = lambda x: b'  %b' % x
        contents = [x for k,v in self.items() for x in (b'%b %b' % (bytes(k),bytes(v))).split(b'\n')]
        return b'\n'.join([
            b'<<',
            *map(formatter, contents),
            b'>>'
        ])

    def __getitem__(self, index):
        return self.value.__getitem__(index)

    def __setitem__(self, key, value):
        return self.value.__setitem__(key, value)

    def __delitem__(self, key):
        return self.value.__delitem__(key)

    def __iter__(self):
        return self.value.__iter__()

    def __len__(self):
        return self.value.__len__()

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


@dataclasses.dataclass
class PdfStream(PdfObject):

    stream_dict: PdfDict = dataclasses.field(default_factory=PdfDict)
    contents: list = dataclasses.field(default_factory=list)

    def __bytes__(self):
        if self.contents is None:
            raise PdfFormatError
        contents = b'\n'.join(map(bytes, self.contents))

        stream_filters = self.stream_dict.get('Filter', [])
        if isinstance(stream_filters, PdfName):
            stream_filters = [stream_filters]
        for stream_filter in stream_filters[::-1]:
            if stream_filter == 'ASCII85Decode':
                # readers may not like the beginning `<~` (such as qpdfview) so this indexes past that
                contents = base64.a85encode(contents, adobe=True)[2:]
            elif stream_filter == 'FlateDecode':
                contents = zlib.compress(contents)
            elif stream_filter == 'DCTDecode':
                im = Image.open(io.BytesIO(contents))
                op = io.BytesIO()
                im.save(op, 'JPEG')
                contents = op.getvalue()
            elif stream_filter == 'ASCIIHexDecode':
                contents = contents.hex().encode('ascii')
            else:
                raise PdfParseError

        self.stream_dict.update({PdfName('Length'): PdfInteger(len(contents))})
        return b'\n'.join([
            bytes(self.stream_dict),
            b'stream',
            contents,
            b'endstream'
        ])

    @property
    def op_map(self):
        return {
            b'q': StateSaveOperation,
            b'Q': StateRestoreOperation,
            b'cm': ConcatenateMatrixOperation,
            b'w': LineWidthOperation,
            b'J': LineCapStyleOperation,
            b'j': LineJoinStyleOperation,
            b'M': MiterLimitOperation,
            b'd': DashPatternOperation,
            b'ri': ColorRenderIntentOperation,
            b'i': FlatnessToleranceOperation,
            b'gs': StateParametersOperation,
        }
    
    def _parse_stream_object(self, io_buffer, _op_args=None):
        _op_args = _op_args or []
        tokens = read_pdf_tokens(io_buffer)
        start_offset = io_buffer.tell()
        first_token = next(tokens, None)
        if first_token is None:
            return None
        elif first_token in self.op_map:
            if len(_op_args) != len(inspect.signature(self.op_map[first_token]).parameters):
                raise PdfParseError
            return self.op_map[first_token](*_op_args)
        elif first_token == b'BT':
            io_buffer.seek(start_offset, io.SEEK_SET)
            return StreamTextObject().parse(io_buffer)
        elif first_token == b'Do':
            return StreamXObject(*_op_args)
        elif first_token == b'm':
            contents = [PathMoveOperation(*_op_args)]
            return StreamPathObject(contents=contents).parse(io_buffer)
        elif first_token == b're':
            contents = [PathRectangleOperation(*_op_args)]
            return StreamPathObject(contents=contents).parse(io_buffer)
        else:
            # must be an instruction arg
            io_buffer.seek(start_offset, io.SEEK_SET)
            _op_args.append(parse_pdf_object(io_buffer).value)
            return self._parse_stream_object(io_buffer, _op_args=_op_args)

    def parse(self, io_buffer, skip_stream_dict=False):
        if self.skip_stream_dict is False:
            self.stream_dict = PdfDict().parse(io_buffer)

        stream_length = int(self.stream_dict['Length'])
        stream_contents = io_buffer.read(stream_length)
        stream_filters = self.stream_dict.get('Filter', [])
        if isinstance(stream_filters, PdfName):
            stream_filters = [stream_filters]
        for stream_filter in stream_filters:
            if stream_filter == 'ASCII85Decode':
                stream_contents = base64.a85decode(stream_contents, adobe=True)
            elif stream_filter == 'FlateDecode':
                stream_contents = zlib.decompress(stream_contents)
            elif stream_filter == 'DCTDecode':
                im = Image.open(io.BytesIO(stream_contents))
                stream_contents = im.tobytes()
            elif stream_filter == 'ASCIIHexDecode':
                stream_contents = bytes.fromhex(stream_contents.decode('ascii'))
            else:
                raise PdfParseError
        stream_buffer = io.BytesIO(stream_contents)

        while True:
            parsed_object = self._parse_stream_object(stream_buffer)
            if parsed_object is None:
                break
            self.contents.append(parsed_object)

        if next(read_pdf_tokens(io_buffer)) != b'endstream':
            raise PdfParseError
        return self


@dataclasses.dataclass
class StateSaveOperation(GraphicsOperation):

    def __bytes__(self):
        return b'q'


@dataclasses.dataclass
class StateRestoreOperation(GraphicsOperation):

    def __bytes__(self):
        return b'Q'


@dataclasses.dataclass
class ConcatenateMatrixOperation(GraphicsOperation):

    a: numbers.Real = 1
    b: numbers.Real = 0
    c: numbers.Real = 0
    d: numbers.Real = 1
    e: numbers.Real = 0
    f: numbers.Real = 0

    @property
    def transformation_matrix(self):
        return \
            [[self.a, self.b, 0],
             [self.c, self.d, 0],
             [self.e, self.f, 1]]

    @transformation_matrix.setter
    def transformation_matrix(self, value):
        [self.a, self.b, _], [self.c, self.d, _], [self.e, self.f, _] = value

    def __bytes__(self):
        values = (self.a, self.b, self.c, self.d, self.e, self.f)
        return b'%b %b %b %b %b %b cm' % tuple(map(PdfReal, values))

    def add_translation(self, x=0, y=0):
        self.transformation_matrix = np.matmul(
            [[1, 0, 0],
             [0, 1, 0],
             [x, y, 1]],
            self.transformation_matrix)

    def add_scaling(self, x=1, y=1):
        self.transformation_matrix = np.matmul(
            [[x, 0, 0],
             [0, y, 0],
             [0, 0, 1]],
            self.transformation_matrix)

    def add_skew(self, angle_a=0, angle_b=0):
        a = math.tan(angle_a*math.pi/180)
        b = math.tan(angle_b*math.pi/180)
        self.transformation_matrix = np.matmul(
            [[1, a, 0],
             [b, 1, 0],
             [0, 0, 1]],
            self.transformation_matrix)

    def add_rotation(self, angle=0):
        c = math.cos(angle*math.pi/180)
        s = math.sin(angle*math.pi/180)
        self.transformation_matrix = np.matmul(
            [[c, s, 0],
             [-s, c, 0],
             [0, 0, 1]],
            self.transformation_matrix)


@dataclasses.dataclass
class LineWidthOperation(GraphicsOperation):

    width: numbers.Real

    def __bytes__(self):
        return b"%b w" % PdfReal(self.width)


@dataclasses.dataclass
class LineCapStyleOperation(GraphicsOperation):

    cap_style: numbers.Real

    def __bytes__(self):
        return b"%b J" % PdfInteger(self.cap_style)


@dataclasses.dataclass
class LineJoinStyleOperation(GraphicsOperation):

    def __init__(self, join_style=None):
        self.join_style = join_style

    def __bytes__(self):
        return b"%b j" % PdfInteger(self.join_style)


@dataclasses.dataclass
class MiterLimitOperation(GraphicsOperation):

    def __init__(self, limit=None):
        self.limit = limit

    def __bytes__(self):
        return b"%b M" % PdfReal(self.limit)


@dataclasses.dataclass
class DashPatternOperation(GraphicsOperation):

    def __init__(self, dash_array=None, dash_phase=None):
        self.dash_array = dash_array
        self.dash_phase = dash_phase

    def __bytes__(self):
        assert any(self.dash_array)  # can't be all zeros
        return b"%b %b d" % (PdfArray(*map(PdfReal(self.dash_array))), PdfReal(self.dash_phase))


@dataclasses.dataclass
class ColorRenderIntentOperation(GraphicsOperation):

    def __init__(self, intent=None):
        self.intent = intent

    def __bytes__(self):
        return b"%b ri" % PdfName(self.intent)


@dataclasses.dataclass
class FlatnessToleranceOperation(GraphicsOperation):

    def __init__(self, flatness=None):
        self.flatness = flatness

    def __bytes__(self):
        assert self.flatness >= 0 and self.flatness <= 100
        return b"%b i" % PdfReal(self.flatness)


@dataclasses.dataclass
class StateParametersOperation(GraphicsOperation):

    def __init__(self, param_dict_name=None):
        self.param_dict_name = param_dict_name

    def __bytes__(self):
        return b"%b gs" % PdfName(self.param_dict_name)


@dataclasses.dataclass
class StreamTextObject(GraphicsObject):

    def __init__(self, contents=None):
        self.contents = contents or []

    def __bytes__(self):
        formatter = lambda x: b'  %b' % x
        contents = map(bytes, self.contents)
        contents = [x for c in contents for x in c.split(b'\n')]
        return b'\n'.join([
            b'BT',
            *map(formatter, contents),
            b'ET'
        ])

    @property
    def op_map(self):
        return {
            b'Tj': TextShowOperation,
            b'TL': TextLeadingOperation,
            b'Tf': TextFontOperation,
            b'Tm': TextMatrixOperation,
            b'T*': TextNextLineOperation,
            b'Tc': TextCharSpaceOperation,
            b'Tw': TextWordSpaceOperation,
            b'Tz': TextScaleOperation,
            b'Tr': TextRenderModeOperation,
            b'Ts': TextRiseOperation,
        }

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
            elif token in self.op_map:
                if len(_op_args) != len(inspect.signature(self.op_map[token]).parameters):
                    raise PdfParseError
                self.contents.append(self.op_map[token](*_op_args))
                _op_args = []
            else:
                io_buffer.seek(start_offset, io.SEEK_SET)
                _op_args.append(parse_pdf_object(io_buffer))
                tokens = read_pdf_tokens(io_buffer)
        return self


@dataclasses.dataclass
class TextFontOperation(GraphicsOperation):

    def __init__(self, font_alias_name=None, size=None):
        self.font_alias_name = font_alias_name
        self.size = size

    def __bytes__(self):
        return b"%b %b Tf" % (PdfName(self.font_alias_name), PdfReal(self.size))


class TextLeadingOperation(GraphicsOperation):

    def __init__(self, leading=None):
        self.leading = leading

    def __bytes__(self):
        return b"%b TL" % PdfReal(self.leading)


class TextMatrixOperation(GraphicsOperation):

    def __init__(self, a=1, b=0, c=0, d=1, e=0, f=0):
        self.transformation_matrix = \
            [[a, b, 0],
             [c, d, 0],
             [e, f, 1]]

    def __bytes__(self):
        [a, b, _], [c, d, _], [e, f, _] = self.transformation_matrix
        return b'%b %b %b %b %b %b Tm' % tuple(map(PdfReal, (a, b, c, d, e, f)))


class TextNextLineOperation(GraphicsOperation):

    def __bytes__(self):
        return b'T*'


class TextShowOperation(GraphicsOperation):

    def __init__(self, text=None):
        self.text = text

    def __bytes__(self):
        return b"%b Tj" % PdfLiteralString(self.text)


class TextCharSpaceOperation(GraphicsOperation):

    def __init__(self, char_space=None):
        self.char_space = char_space

    def __bytes__(self):
        return b"%b Tc" % PdfReal(self.char_space)


class TextWordSpaceOperation(GraphicsOperation):

    def __init__(self, word_space=None):
        self.word_space = word_space

    def __bytes__(self):
        return b"%b Tw" % PdfReal(self.word_space)


class TextScaleOperation(GraphicsOperation):

    def __init__(self, scale=None):
        self.scale = scale

    def __bytes__(self):
        return b"%b Tz" % PdfReal(self.scale)


class TextRenderModeOperation(GraphicsOperation):

    def __init__(self, render_mode=None):
        self.render_mode = render_mode

    def __bytes__(self):
        return "%b Tr" % PdfInteger(self.render_mode)


class TextRiseOperation(GraphicsOperation):

    def __init__(self, rise=None):
        self.rise = rise

    def __bytes__(self):
        return b"%b Ts" % PdfReal(self.rise)


class PdfArray(collections.abc.MutableSequence, PdfObject):

    def __init__(self, value=None):
        self.value = list(value or [])

    def __getitem__(self, index):
        return self.value.__getitem__(index)

    def __setitem__(self, index, value):
        return self.value.__setitem__(index, value)

    def __delitem__(self, index):
        return self.value.__delitem__(index)

    def __len__(self):
        return self.value.__len__()

    def insert(self, index, value):
        return self.value.insert(index, value)

    def __add__(self, value):
        return self.value.__add__(list(value))

    def __bytes__(self):
        contents = list(map(bytes, self))
        if len(contents) == 1 and b'\n' not in contents[0]:
            contents = b' '.join(contents)
            return b'[ %b ]' % contents
        else:
            formatter = lambda x: b'  %b' % x
            contents = [x for c in contents for x in c.split(b'\n')]
            return b'\n'.join([
                b'[',
                *map(formatter, contents),
                b']'
            ])


class PdfName(PdfString):

    def __repr__(self):
        return self.value.__repr__()

    def __bytes__(self):
        result = bytearray(b'/')
        name_bytes = self.value.encode('us-ascii')
        for b in name_bytes:
            if b in ALLOWED_NAME_CHARS:
                result.append(b)
            else:
                result.extend(b"#%02X" % b)
        return bytes(result)


class PdfComment(PdfString):

    def __bytes__(self):
        return b'%%%b' % self.value.encode('utf-8')


class PdfIndirectObject(PdfObject):

    def __init__(self):
        self.object_number = None
        self.generation_number = None
        self.attached = False
        self.free = False
        self.next_free_object = None
        self.ref = None
        self.contents = None
        self.pdf_section = None

    def __bytes__(self):
        if self.attached is False:
            raise PdfFormatError
        contents = bytes(self.contents)
        if not isinstance(self.contents, PdfStream):
            formatter = lambda x: b'  %b' % x
            contents = b'\n'.join(map(formatter, contents.split(b'\n')))
        return b'\n'.join([
            b' '.join([
                bytes(PdfInteger(self.object_number)),
                bytes(PdfInteger(self.generation_number)),
                b'obj'
            ]),
            contents,
            b'endobj'
        ])

    @property
    def object_key(self):
        return (self.object_number, self.generation_number)

    def attach(self, object_number, generation_number, pdf_section, contents):
        # attached means the object has been given identifying information
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True
        self.ref = PdfIndirectObjectRef(object_number, generation_number)
        self.pdf_section = pdf_section
        self.contents = contents
        return self

    def release(self, next_free_object):
        self.next_free_object = next_free_object
        self.free = True

    def parse(self, io_buffer):
        line = next(read_lines(io_buffer), None)
        if line is None:
            raise PdfParseError
        line_parts = line.split()
        if len(line_parts) != 3 or line_parts[2] != b'obj':
            raise PdfParseError
        self.object_number = line_parts[0]
        self.generation_number = line_parts[1]
        self.contents = parse_pdf_object(io_buffer)
        final_token = next(read_pdf_tokens(io_buffer), None)
        if final_token != b'endobj':
            raise PdfParseError
        return self


class PdfIndirectObjectRef(PdfObject):

    def __init__(self, object_number, generation_number):
        if not (isinstance(object_number, int) or isinstance(object_number, float)):
            raise PdfValueError
        if not (isinstance(generation_number, int) or isinstance(generation_number, float)):
            raise PdfValueError
        self.object_number = object_number
        self.generation_number = generation_number

    def __bytes__(self):
        return b' '.join([
            bytes(PdfInteger(self.object_number)),
            bytes(PdfInteger(self.generation_number)),
            b'R'
        ])

    @property
    def object_key(self):
        return (self.object_number, self.generation_number)


class StreamXObject(GraphicsObject):

    def __init__(self, alias_name=None):
        self.alias_name = alias_name

    def __bytes__(self):
        return b'%b Do' % self.alias_name


class StreamPathObject(GraphicsObject):

    def __init__(self, contents=None):
        self.contents = contents or []

    def __bytes__(self):
        contents = map(bytes, self.contents)
        formatter = lambda x: b'  %b' % x
        return b'\n'.join(map(formatter, [x for c in contents for x in c.split(b'\n')]))

    @property
    def op_map(self):
        return {
            b'm': PathMoveOperation,
            b're': PathRectangleOperation,
            b'l': PathLineOperation,
            b'c': PathCurveOperation,
            b'v': PathCurve2Operation,
            b'c': PathCurve3Operation,
            b'h': PathCloseOperation,
        }

    @property
    def path_paint_op_map(self):
        return {
            b'S': PathStrokeOperation,
            b's': PathCloseStrokeOperation,
            b'f': PathFillOperation,
            b'F': _PathFillOperation,
            b'f*': PathFillEvenOddOperation,
            b'B': PathFillStrokeOperation,
            b'B*': PathFillEvenOddStrokeOperation,
            b'b': PathCloseFillStrokeOperation,
            b'b*': PathCloseFillEvenOddStrokeOperation,
            b'n': PathNoOpOperation,
        }

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
            if token in self.path_paint_op_map:
                if len(_op_args) != len(inspect.signature(self.path_paint_op_map[token]).parameters):
                    raise PdfParseError
                self.contents.append(self.path_paint_op_map[token](*_op_args))
                break
            elif token in (b'W', b'W*'):
                contents = [PathClipOperation()] if token == b'W' else [PathClipEvenOddOperation()]
                self.contents.append(StreamClippingPathObject(contents=contents).parse(io_buffer))
                break
            elif token in self.op_map:
                if len(_op_args) != len(inspect.signature(self.op_map[token]).parameters):
                    raise PdfParseError
                self.contents.append(self.op_map[token](*_op_args))
                _op_args = []
            else:
                io_buffer.seek(start_offset, io.SEEK_SET)
                _op_args.append(parse_pdf_object(io_buffer))
                tokens = read_pdf_tokens(io_buffer)
        return self


class PathMoveOperation(GraphicsOperation):

    def __init__(self, x=None, y=None):
        self.x = x
        self.y = y

    def __bytes__(self):
        return b'%b %b m' % (PdfReal(self.x), PdfReal(self.y))


class PathRectangleOperation(GraphicsOperation):

    def __init__(self, x=None, y=None, width=None, height=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def __bytes__(self):
        return b'%b %b %b %b re' % (PdfReal(self.x), PdfReal(self.y), PdfReal(self.width), PdfReal(self.height))


class PathLineOperation(GraphicsOperation):

    def __init__(self, x=None, y=None):
        self.x = x
        self.y = y

    def __bytes__(self):
        return b'%b %b l' % (PdfReal(self.x), PdfReal(self.y))


class PathCurveOperation(GraphicsOperation):

    def __init__(self, x1=None, y1=None, x2=None, y2=None, x3=None, y3=None):
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.x3 = x3
        self.y3 = y3

    def __bytes__(self):
        return b'%b %b %b %b %b %b c' % tuple(map(PdfReal, (self.x1, self.y1, self.x2, self.y2, self.x3, self.y3)))


class PathCurve2Operation(GraphicsOperation):

    def __init__(self, x2=None, y2=None, x3=None, y3=None):
        self.x2 = x2
        self.y2 = y2
        self.x3 = x3
        self.y3 = y3

    def __bytes__(self):
        return b'%b %b %b %b v' % (PdfReal(self.x2), PdfReal(self.y2), PdfReal(self.x3), PdfReal(self.y3))


class PathCurve3Operation(GraphicsOperation):

    def __init__(self, x1=None, y1=None, x3=None, y3=None):
        self.x1 = x1
        self.y1 = y1
        self.x3 = x3
        self.y3 = y3

    def __bytes__(self):
        return b'%b %b %b %b c' % (PdfReal(self.x1), PdfReal(self.y1), PdfReal(self.x3), PdfReal(self.y3))


class PathCloseOperation(GraphicsOperation):

    def __bytes__(self):
        return b'h'


class PathStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b'S'


class PathCloseStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b's'


class PathFillOperation(GraphicsOperation):

    def __bytes__(self):
        return b'f'


class _PathFillOperation(GraphicsOperation):

    def __bytes__(self):
        return b'F'


class PathFillEvenOddOperation(GraphicsOperation):

    def __bytes__(self):
        return b'f*'


class PathFillStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b'B'


class PathFillEvenOddStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b'B*'


class PathCloseFillStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b'b'


class PathCloseFillEvenOddStrokeOperation(GraphicsOperation):

    def __bytes__(self):
        return b'b*'


class PathNoOpOperation(GraphicsOperation):

    def __bytes__(self):
        return b'n'


class PathClipOperation(GraphicsOperation):

    def __bytes__(self):
        return b'W'


class PathClipEvenOddOperation(GraphicsOperation):

    def __bytes__(self):
        return b'W*'


class StreamClippingPathObject(GraphicsObject):

    def __init__(self, contents=None):
        self.contents = contents or []

    def __bytes__(self):
        contents = map(bytes, self.contents)
        return b'\n'.join(contents)

    @property
    def path_paint_op_map(self):
        return {
            b'S': PathStrokeOperation,
            b's': PathCloseStrokeOperation,
            b'f': PathFillOperation,
            b'F': _PathFillOperation,
            b'f*': PathFillEvenOddOperation,
            b'B': PathFillStrokeOperation,
            b'B*': PathFillEvenOddStrokeOperation,
            b'b': PathCloseFillStrokeOperation,
            b'b*': PathCloseFillEvenOddStrokeOperation,
            b'n': PathNoOpOperation,
        }

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
            if token in self.path_paint_op_map:
                if len(_op_args) != len(inspect.signature(self.path_paint_op_map[token]).parameters):
                    raise PdfParseError
                self.contents.append(self.path_paint_op_map[token](*_op_args))
                break
            else:
                io_buffer.seek(start_offset, io.SEEK_SET)
                _op_args.append(parse_pdf_object(io_buffer))
                tokens = read_pdf_tokens(io_buffer)
        return self
