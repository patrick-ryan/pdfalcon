import codecs


PDFDocEncoding = {
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


def encode_text(s):
    return codecs.BOM_UTF16_BE + s.encode("utf_16_be")


def decode_text(b):
    if b[: len(codecs.BOM_UTF16_BE)] == codecs.BOM_UTF16_BE:
        return b[len(codecs.BOM_UTF16_BE) :].decode("utf_16_be")
    else:
        return "".join(PDFDocEncoding.get(byte, chr(byte)) for byte in b)
