#!/usr/bin/env python3
"""Read-only DOCX text, reference, caption and revision audit. No layout claims."""
import argparse
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
W = '{' + NS['w'] + '}'
REF_HEADING = re.compile(r'^\s*(?:\d+[.、\s]*)?(参考文献|主要参考文献|References|Bibliography)\s*$', re.I)
CITATION = re.compile(r'\[\s*([0-9][0-9\s,，、\-–]*)\]')
REF_ENTRY = re.compile(r'^\s*\[([0-9]+)\]\s*(.*)')
CAPTION = re.compile(r'^\s*((?:图|表)\s*\d+(?:[.\-－]\d+)*)\s*(.*)')
PLACEHOLDER = re.compile(r'TODO|TBD|待补充|待核实|待填写|\[插入[^\]]*\]|<待[^>]*>|【待[^】]*】|\ue200|\ue201|turn\d+(?:search|view)\d+', re.I)


def paragraph_text(paragraph):
    chunks = []
    def visit(element):
        for child in element:
            if child.tag == W + 'p':
                continue
            if child.tag == W + 't':
                chunks.append(child.text or '')
            visit(child)
    visit(paragraph)
    return ''.join(chunks)


def package_part_diff(before, after):
    def hashes(path):
        with zipfile.ZipFile(path) as z:
            return {n: hashlib.sha256(z.read(n)).hexdigest()
                    for n in z.namelist() if not n.endswith('/')}
    a, b = hashes(before), hashes(after)
    common = set(a) & set(b)
    return {'added': sorted(set(b) - set(a)), 'removed': sorted(set(a) - set(b)),
            'changed': sorted(n for n in common if a[n] != b[n]),
            'unchanged': sorted(n for n in common if a[n] == b[n]),
            'note': 'Byte hashes compare decompressed package parts, not semantic equivalence or authorization. Inspect changes to media, styles, numbering, headers, footers and relationships; document.xml can contain both intended and unintended edits.'}


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        body = root.find('w:body', NS)
        if body is None:
            raise ValueError('Main document body is missing.')
        styles = {}
        if 'word/styles.xml' in z.namelist():
            sr = ET.fromstring(z.read('word/styles.xml'))
            styles = {st.get(W + 'styleId'): st for st in sr.findall('w:style', NS)}
        def has_numbering(paragraph, style_id):
            if paragraph.find('w:pPr/w:numPr', NS) is not None:
                return True
            seen = set()
            while style_id in styles and style_id not in seen:
                seen.add(style_id)
                st = styles[style_id]
                if st.find('w:pPr/w:numPr', NS) is not None:
                    return True
                base = st.find('w:basedOn', NS)
                style_id = base.get(W + 'val') if base is not None else None
            return False
        paragraphs = []
        for p in body.findall('.//w:p', NS):
            text = paragraph_text(p)
            if text.strip():
                ps = p.find('w:pPr/w:pStyle', NS)
                style_id = ps.get(W + 'val') if ps is not None else None
                paragraphs.append({'index': len(paragraphs) + 1, 'text': text,
                                   'style': style_id, 'automatic_numbering': has_numbering(p, style_id)})
        ancillary = {}
        for name in z.namelist():
            if re.match(r'word/(?:footnotes|endnotes|header\d+|footer\d+)\.xml$', name):
                r = ET.fromstring(z.read(name))
                ancillary[name] = ''.join(t.text or '' for t in r.findall('.//w:t', NS))
        return {'paragraphs': paragraphs, 'ancillary_text': ancillary,
                'image_relationship_uses': len(root.findall('.//a:blip', NS)),
                'embedded_media_files': len([n for n in z.namelist() if n.startswith('word/media/')]),
                'tracked_insertions': len(root.findall('.//w:ins', NS)),
                'tracked_deletions': len(root.findall('.//w:del', NS)),
                'comments_part_present': 'word/comments.xml' in z.namelist(),
                'xml_root': root}


def expand_citation(s):
    """Parse one numeric bracket; reject malformed or unbounded ranges safely."""
    result = []
    for part in re.split(r'[,，、]', s):
        match = re.fullmatch(r'\s*([1-9][0-9]*)(?:\s*[-–]\s*([1-9][0-9]*))?\s*', part)
        if not match:
            raise ValueError('Expected a positive reference number or a single increasing range in each comma-separated item.')
        lo = int(match[1])
        hi = int(match[2]) if match[2] is not None else lo
        if hi < lo:
            raise ValueError('Descending reference range cannot be resolved.')
        if hi - lo >= 1000:
            raise ValueError('Reference range exceeds the 1000-entry safety limit; inspect it manually.')
        result.extend(range(lo, hi + 1))
    return result


def audit(path, numeric):
    data = read_docx(path)
    paragraphs = data['paragraphs']
    headings = [p['index'] for p in paragraphs if REF_HEADING.match(p['text'])]
    ref_start = headings[-1] if headings else None
    issues = []
    for p in paragraphs:
        if PLACEHOLDER.search(p['text']):
            issues.append({'kind': 'placeholder_or_tool_marker', 'paragraph': p['index'], 'text': p['text'][:180]})
    captions = []
    for p in paragraphs:
        m = CAPTION.match(p['text'])
        if m:
            captions.append({'label': re.sub(r'\s+', '', m[1]), 'paragraph': p['index'], 'text': p['text']})
    for label, count in Counter(c['label'] for c in captions).items():
        if count > 1:
            issues.append({'kind': 'possible_duplicate_caption', 'label': label, 'count': count})
    references = []
    citation_ids = []
    unresolved_automatic_references = []
    if numeric:
        if ref_start is None:
            issues.append({'kind': 'reference_heading_not_found', 'note': 'Numeric reference checks are incomplete.'})
        for p in paragraphs:
            if ref_start is not None and p['index'] > ref_start:
                m = REF_ENTRY.match(p['text'])
                if m:
                    references.append({'id': int(m[1]), 'paragraph': p['index'], 'text': m[2]})
                elif p['automatic_numbering']:
                    unresolved_automatic_references.append(p['index'])
            elif ref_start is None or p['index'] < ref_start:
                for m in CITATION.finditer(p['text']):
                    try:
                        citation_ids.extend(expand_citation(m[1]))
                    except ValueError as exc:
                        issues.append({'kind': 'malformed_numeric_citation',
                                       'paragraph': p['index'], 'citation': m[0],
                                       'note': str(exc) + ' This bracket was skipped; scanning continued.'})
        ref_ids = [r['id'] for r in references]
        if unresolved_automatic_references:
            issues.append({'kind': 'bibliography_automatic_numbering_not_resolved',
                           'paragraphs': unresolved_automatic_references,
                           'note': 'Missing/unused-reference comparison was skipped; inspect Word numbering and rendered labels.'})
        if ref_start is not None and not unresolved_automatic_references:
            for n, count in Counter(ref_ids).items():
                if count > 1:
                    issues.append({'kind': 'duplicate_reference_id', 'id': n})
            missing = sorted(set(citation_ids) - set(ref_ids))
            unused = sorted(set(ref_ids) - set(citation_ids))
            if missing:
                issues.append({'kind': 'citation_without_reference', 'ids': missing})
            if unused:
                issues.append({'kind': 'reference_not_cited_in_main_body', 'ids': unused})
        first_order = list(dict.fromkeys(citation_ids))
        if first_order != sorted(first_order):
            issues.append({'kind': 'first_citation_order_not_ascending', 'order': first_order,
                           'note': 'Only a defect when the required citation style uses order of appearance.'})
    result = {k: v for k, v in data.items() if k not in ('xml_root', 'ancillary_text')}
    result.update({'file': str(path), 'numeric_reference_checks_enabled': numeric,
                   'reference_heading_paragraph': ref_start, 'references': references,
                   'main_body_citation_ids': sorted(set(citation_ids)), 'possible_captions': captions,
                   'ancillary_parts': list(data['ancillary_text']), 'issues': issues,
                   'limits': ['Read-only heuristic audit; not a visual or scientific review.',
                              'Paragraph text includes tables and inserted revisions; deleted text is not included.',
                              'Numeric citation checks require literal bracket numbers; fields with no cached text and nonstandard citation formats may be missed. Automatic list numbering is flagged, not resolved.',
                              'Body-only citation checks exclude notes, headers, footers, text inside images and bibliography continuation text.',
                              'Caption detection can match prose beginning with a figure or table number.',
                              'Review full text and render every page before delivery.']})
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('docx', type=Path)
    ap.add_argument('--numeric-citations', action='store_true', help='Enable heuristic [1] / [1-3] checks.')
    ap.add_argument('--compare', type=Path, help='Earlier DOCX; report text changes and uncompressed package-part byte differences.')
    ap.add_argument('--out', type=Path, help='JSON report destination; stdout if omitted.')
    args = ap.parse_args()
    try:
        if args.out:
            for source in (args.docx, args.compare):
                if source and (args.out.resolve() == source.resolve() or
                               (args.out.exists() and source.exists() and args.out.samefile(source))):
                    raise ValueError('The report output must not overwrite either input DOCX.')
        result = audit(args.docx, args.numeric_citations)
        if args.compare:
            before = [p['text'] for p in read_docx(args.compare)['paragraphs']]
            after = [p['text'] for p in result['paragraphs']]
            result['text_diff'] = list(difflib.unified_diff(before, after,
                                      fromfile=str(args.compare), tofile=str(args.docx), lineterm=''))
            result['package_part_diff'] = package_part_diff(args.compare, args.docx)
            result['limits'].append('Text and package-part differences do not prove visual equivalence, effective styles, technical correctness or that edits stayed in the authorized scope.')
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if args.out:
            args.out.write_text(rendered, encoding='utf-8')
        else:
            print(rendered, end='')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f'Audit failed: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
