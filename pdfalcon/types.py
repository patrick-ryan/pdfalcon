import abc
import collections
import io
import math
import numbers
import numpy as np
import textwrap

from pdfalcon.exceptions import PdfFormatError, PdfParseError, PdfValueError
from pdfalcon.utils import read_pdf_tokens, read_lines


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
    elif first_token == b'w':
        if len(_op_args) != 1:
            raise PdfParseError
        return LineWidthOperation(width=_op_args[0])
    elif first_token == b'J':
        if len(_op_args) != 1:
            raise PdfParseError
        return LineCapStyleOperation(cap_style=_op_args[0])
    elif first_token == b'j':
        if len(_op_args) != 1:
            raise PdfParseError
        return LineJoinStyleOperation(join_style=_op_args[0])
    elif first_token == b'M':
        if len(_op_args) != 1:
            raise PdfParseError
        return MiterLimitOperation(limit=_op_args[0])
    elif first_token == b'd':
        if len(_op_args) != 2:
            raise PdfParseError
        return DashPatternOperation(dash_array=_op_args[0], dash_phase=_op_args[1])
    elif first_token == b'ri':
        if len(_op_args) != 1:
            raise PdfParseError
        return ColorRenderIntentOperation(intent=_op_args[0])
    elif first_token == b'i':
        if len(_op_args) != 1:
            raise PdfParseError
        return FlatnessToleranceOperation(flatness=_op_args[0])
    elif first_token == b'gs':
        if len(_op_args) != 1:
            raise PdfParseError
        return StateParametersOperation(param_dict_name=_op_args[0])
    elif first_token == b'BT':
        io_buffer.seek(start_offset, io.SEEK_SET)
        return StreamTextObject().parse(io_buffer)
    else:
        # must be an instruction arg
        io_buffer.seek(start_offset, io.SEEK_SET)
        _op_args.append(parse_pdf_object(io_buffer))
        return parse_stream_object(io_buffer, _op_args=_op_args)


class BaseObject(abc.ABC):

    def __eq__(self, obj):
        return type(obj) == type(self) and vars(obj) == vars(self)

    @abc.abstractmethod
    def format(self):
        pass

    def clone(self):
        new_obj = self.__class__()
        for k,v in vars(self):
            if isinstance(v, list):
                setattr(new_obj, k, [x.clone() for x in v])
            elif isinstance(v, dict):
                setattr(new_obj, k, {k_.clone(): v_.clone() for k_, v_ in v})
            else:
                setattr(new_obj, k, v)
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

    def format(self):
        return 'true' if self.value is True else 'false'


class PdfNull(PdfObject):

    def format(self):
        return 'null'


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

    def __float__(self):
        value = self.value.__float__()
        return self.__class__(value)

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


class PdfInteger(PdfNumeric):

    def __init__(self, value=None):
        self.value = int(value or 0)

    def format(self):
        return str(self.value)


class PdfReal(PdfNumeric):

    def __init__(self, value=None):
        self.value = float(value or 0)

    def format(self):
        return str(self.value)


class PdfString(PdfObject):

    def __init__(self, value=None):
        self.value = str(value or '')

    def __getitem__(self, index):
        return self.value[index]

    def __eq__(self, str_):
        return str.__eq__(self.value, str_)

    def __hash__(self):
        return str.__hash__(self.value)


class PdfHexString(PdfString):

    _str = '<{contents}>'

    def format(self):
        return self._str.format(contents=self.value)


class PdfLiteralString(PdfString):

    _str = '({contents})'

    def format(self):
        return self._str.format(contents=self.value)


class PdfDict(collections.abc.MutableMapping, PdfObject):

    _str = textwrap.dedent('''
        <<
        {contents}
        >>
    ''').strip()

    def __init__(self, value=None):
        self.value = dict(value or {})

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

    def format(self):
        contents = '\n'.join([f"{k.format()} {v.format()}" for k,v in self.items()])
        contents = textwrap.indent(contents, '  ')
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
        stream_dict = PdfDict(self.stream_dict or {})
        stream_dict.update({PdfName('Length'): PdfInteger(len(contents))})
        stream_dict = stream_dict.format()
        return self._str.format(stream_dict=stream_dict, contents=contents)

    def parse(self, io_buffer):
        # TODO: apply decoding filters, handle special whitespace
        if self.stream_dict is None:
            self.stream_dict = PdfDict().parse(io_buffer)
        while True:
            parsed_object = parse_stream_object(io_buffer)
            if parsed_object is None:
                break
            self.contents.append(parsed_object)
        return self


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
        return f"{self.font_alias_name.format()} {self.size} Tf"


class LineWidthOperation(GraphicsOperation):

    def __init__(self, width=None):
        self.width = width

    def format(self):
        return f"{self.width} w"


class LineCapStyleOperation(GraphicsOperation):

    def __init__(self, cap_style=None):
        self.cap_style = cap_style

    def format(self):
        return f"{self.cap_style} J"


class LineJoinStyleOperation(GraphicsOperation):

    def __init__(self, join_style=None):
        self.join_style = join_style

    def format(self):
        return f"{self.join_style} j"


class MiterLimitOperation(GraphicsOperation):

    def __init__(self, limit=None):
        self.limit = limit

    def format(self):
        return f"{self.limit} M"


class DashPatternOperation(GraphicsOperation):

    def __init__(self, dash_array=None, dash_phase=None):
        self.dash_array = dash_array
        self.dash_phase = dash_phase

    def format(self):
        return f"{self.dash_array} {self.dash_phase} d"


class ColorRenderIntentOperation(GraphicsOperation):

    def __init__(self, intent=None):
        self.intent = intent

    def format(self):
        return f"{self.intent} ri"


class FlatnessToleranceOperation(GraphicsOperation):

    def __init__(self, flatness=None):
        self.flatness = flatness

    def format(self):
        return f"{self.flatness} i"


class StateParametersOperation(GraphicsOperation):

    def __init__(self, param_dict_name=None):
        self.param_dict_name = param_dict_name

    def format(self):
        return f"{self.param_dict_name.format()} gs"


class TextFontOperation(GraphicsOperation):

    def __init__(self, font_alias_name=None, size=None):
        self.font_alias_name = font_alias_name
        self.size = size

    def format(self):
        return f"{self.font_alias_name.format()} {self.size} Tf"


class TextLeadingOperation(GraphicsOperation):

    def __init__(self, leading=None):
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


class TextCharSpaceOperation(GraphicsOperation):

    def __init__(self, char_space=None):
        self.char_space = char_space

    def format(self):
        return f"{self.char_space} Tc"


class TextWordSpaceOperation(GraphicsOperation):

    def __init__(self, word_space=None):
        self.word_space = word_space

    def format(self):
        return f"{self.word_space} Tw"


class TextScaleOperation(GraphicsOperation):

    def __init__(self, scale=None):
        self.scale = scale

    def format(self):
        return f"{self.scale} Tz"


class TextRenderModeOperation(GraphicsOperation):

    def __init__(self, render_mode=None):
        self.render_mode = render_mode

    def format(self):
        return f"{self.render_mode} Tr"


class TextRiseOperation(GraphicsOperation):

    def __init__(self, rise=None):
        self.rise = rise

    def format(self):
        return f"{self.rise} Ts"


class StreamTextObject(GraphicsObject):

    def __init__(self, contents=None):
        self.contents = contents or []

    def format(self):
        output_lines = [
            "BT ",
            *[textwrap.indent(c.format(), '  ') for c in self.contents],
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
            elif token == b'Tc':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextCharSpaceOperation(char_space=_op_args[0]))
                _op_args = []
            elif token == b'Tw':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextWordSpaceOperation(word_space=_op_args[0]))
                _op_args = []
            elif token == b'Tz':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextScaleOperation(scale=_op_args[0]))
                _op_args = []
            elif token == b'Tr':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextRenderModeOperation(render_mode=_op_args[0]))
                _op_args = []
            elif token == b'Ts':
                if len(_op_args) != 1:
                    raise PdfParseError
                self.contents.append(TextRiseOperation(rise=_op_args[0]))
                _op_args = []
            else:
                io_buffer.seek(start_offset, io.SEEK_SET)
                _op_args.append(parse_pdf_object(io_buffer))
                tokens = read_pdf_tokens(io_buffer)
        return self


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

    def format(self):
        fmt = lambda x: x.format()
        contents = map(fmt, self)
        if len(self) == 1:
            contents = ' '.join(contents)
            return f"[ {contents} ]"
        else:
            contents = textwrap.indent('\n'.join(contents), '  ')
            return f"[\n{contents}\n]"


class PdfName(PdfString):

    _str = '/{contents}'

    def format(self):
        return self._str.format(contents=self.value)


class PdfComment(PdfString):

    _str = '%{contents}'

    def format(self):
        return self._str.format(contents=self.value)


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
        contents = self.contents.format()
        if not isinstance(self.contents, PdfStream):
            contents = textwrap.indent(contents, '  ')
        return self._str.format(
            object_number=self.object_number,
            generation_number=self.generation_number,
            contents=contents
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
