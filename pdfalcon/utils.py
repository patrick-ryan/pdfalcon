import io


from pdfalcon.exceptions import PdfBuildError


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
    token_end_offset = 0
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
