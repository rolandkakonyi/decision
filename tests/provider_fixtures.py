"""Provider wire fixtures shared by native black-box transport tests."""

QUESTIONS = {'present': {'type': 'noul', 'instructions': 'Is a dog visible?'},
 'breed': {'type': 'choice',
           'instructions': 'Which breed?',
           'criteria': {'french': 'French bulldog', 'english': 'English bulldog'}},
 'visible': {'type': 'score', 'instructions': 'How visible?', 'criteria': ['Hidden', 'Visible']}}

OPENAI_RESPONSE = {'model': 'gpt-6-luna',
 'usage': {'input_tokens': 10, 'output_tokens': 0},
 'answers': [{'name': 'present', 'type': 'predicate', 'probability': 0.9},
             {'name': 'breed',
              'type': 'choice',
              'choice': 'french',
              'confidence': 0.8,
              'probabilities': [{'value': 'french', 'probability': 0.9},
                                {'value': 'english', 'probability': 0.1}]},
             {'name': 'visible',
              'type': 'score',
              'score': 0.9,
              'confidence': 0.8,
              'probabilities': [{'value': 0, 'label': '0', 'probability': 0.1},
                                {'value': 1, 'label': '1', 'probability': 0.9}]}]}

GEMINI_RESPONSE = {'answers': {'present': {'type': 'noul', 'noul': 0.9, 'confidence': 0.9},
             'breed': {'type': 'choice',
                       'choice': 'french',
                       'confidence': 0.9,
                       'probabilities': {'french': 0.9, 'english': 0.1}},
             'visible': {'type': 'score',
                         'score': 0.9,
                         'confidence': 0.9,
                         'legend': {'0': 'Hidden', '1': 'Visible'},
                         'probabilities': {'0': 0.1, '1': 0.9}}},
 'usage': {'total_input_tokens': 10,
           'total_output_tokens': 0,
           'total_tokens': 10,
           'input_tokens_by_modality': [{'modality': 'text', 'tokens': 10}]}}

CLOUDFLARE_RESPONSE = {'success': True,
 'errors': [],
 'messages': [],
 'result': {'model': 'clef',
            'usage': {'input_tokens': 10, 'output_tokens': 0},
            'answers': {'present': {'type': 'noul', 'noul': 0.9},
                        'breed': {'type': 'choice',
                                  'choice': 'french',
                                  'confidence': 0.9,
                                  'probabilities': {'french': 0.9, 'english': 0.1}},
                        'visible': {'type': 'score',
                                    'score': 1,
                                    'confidence': 0.9,
                                    'legend': {'0': 'Hidden', '1': 'Visible'},
                                    'probabilities': {'0': 0.1, '1': 0.9}}}}}

def png_bytes():
    import struct
    import zlib
    """A synthetic valid 280x280 RGB PNG; no user image or model inference."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\x00" + b"\xff\x00\x00" * 280) * 280
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
