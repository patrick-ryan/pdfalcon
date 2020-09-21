import io


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
    cur_offset = io_buffer.tell()
    next_block_start = cur_offset
    while True:
        io_buffer.seek(next_block_start, io.SEEK_SET)
        block = io_buffer.read(block_size)
        next_block_start = io_buffer.tell()
        if not block:
            break

        for char in block:
            cur_offset += 1
            char = b'%c' % char  # convert raw byte to byte str
            if char in delimiters:
                if cur_token != b'':
                    # end of token
                    io_buffer.seek(cur_offset-1, io.SEEK_SET)
                    yield cur_token

                    cur_token = b''
                io_buffer.seek(cur_offset, io.SEEK_SET)
                yield char
            elif char in whitespace_chars:
                if cur_token != b'':
                    # end of token
                    io_buffer.seek(cur_offset-1, io.SEEK_SET)
                    yield cur_token

                    cur_token = b''
            else:
                cur_token += char

    if cur_token:
        io_buffer.seek(cur_offset, io.SEEK_SET)
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
