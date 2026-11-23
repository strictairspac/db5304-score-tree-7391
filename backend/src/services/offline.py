"""Offline reconciliation for a saved catalog fingerprint.

Before the warehouse picks scores offline it stores the catalog fingerprint of
an inspection run.  With no network it then reconciles that fingerprint and the
borrowing-number segment against the range lending slip.

The fingerprint only carries what may travel with the slip: page capacity, the
inspected interval, every catalog number, and the title / available copies of
in-range scores.  Titles and inventory of scores outside the interval never
enter the fingerprint or the accompanying material; the out-of-range summary
lists numbers only and is capped at ``OUT_OF_RANGE_LIMIT`` entries.  A catalog
whose out-of-range part cannot fit in that summary is refused a slip.
"""

import hashlib

from ..utils.encoding import canonical

FINGERPRINT_ALGORITHM = 'SHA-256'
FINGERPRINT_ENCODING = 'UTF-8 规范 JSON（键排序、无多余空格）'
FINGERPRINT_SCOPE = '页容量、借阅编号区间、全部曲谱编号、区间内谱名与可借份数'
MATERIAL_VERSION = 1
OUT_OF_RANGE_LIMIT = 40

NUMBER_KEYS = ('number', '曲谱编号', '编号')
TITLE_KEYS = ('title', '谱名')
COPIES_KEYS = ('available_copies', '可借份数', 'copies')
START_KEYS = ('start', '起始编号', 'range_start')
END_KEYS = ('end', '结束编号', 'range_end')


def _pick(row, keys):
    for key in keys:
        if key in row:
            return row[key]
    return None


def _as_int(value, label):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    raise ValueError(f'{label}必须为整数')


def _fingerprint_basis(page_capacity, start, end, catalog_numbers, in_range):
    return {
        'page_capacity': page_capacity,
        'range_start': start,
        'range_end': end,
        'catalog_numbers': sorted(set(catalog_numbers)),
        'in_range': sorted(
            (
                {'number': item['number'], 'title': item['title'],
                 'available_copies': item['available_copies']}
                for item in in_range
            ),
            key=lambda item: item['number'],
        ),
    }


def _digest(basis) -> str:
    return hashlib.sha256(canonical(basis)).hexdigest()


def build_fingerprint(capacity: int, first: int, last: int, scores) -> dict:
    """Freeze the catalog facts that may leave the library with the slip."""
    ordered = sorted(scores, key=lambda score: score.number)
    catalog_numbers = [score.number for score in ordered]
    in_range = [
        {'number': score.number, 'title': score.title,
         'available_copies': score.available_copies}
        for score in ordered if first <= score.number <= last
    ]
    basis = _fingerprint_basis(capacity, first, last, catalog_numbers, in_range)
    return {
        'algorithm': FINGERPRINT_ALGORITHM,
        'encoding': FINGERPRINT_ENCODING,
        'scope': FINGERPRINT_SCOPE,
        'digest': _digest(basis),
        'page_capacity': capacity,
        'range': {'start': first, 'end': last},
        'catalog_numbers': basis['catalog_numbers'],
        'in_range': basis['in_range'],
    }


def outside_numbers(numbers, first: int, last: int) -> list[int]:
    """Numbers of a catalog that fall outside the closed interval."""
    return sorted(number for number in numbers if not first <= number <= last)


def build_material(fingerprint, lending_slip=None) -> dict:
    """Assemble the downloadable material that travels with the slip.

    Only numbers of out-of-range scores appear here, never their titles or
    inventory.  When that number-only summary would exceed
    ``OUT_OF_RANGE_LIMIT`` the slip is refused instead of being issued.
    """
    saved = _read_fingerprint(fingerprint)
    start, end = saved['start'], saved['end']
    if start is None or end is None:
        raise ValueError('目录指纹缺少借阅编号段')
    outside = outside_numbers(saved['catalog_numbers'], start, end)
    if len(outside) > OUT_OF_RANGE_LIMIT:
        raise ValueError(
            f'区间外曲谱共 {len(outside)} 种，超过随单摘要容量 {OUT_OF_RANGE_LIMIT} 条，拒绝出单')
    in_range_count = len([number for number in saved['catalog_numbers'] if start <= number <= end])
    return {
        'version': MATERIAL_VERSION,
        'segment': {'start': start, 'end': end},
        'page_capacity': saved['page_capacity'],
        'fingerprint': _raw_fingerprint(fingerprint),
        'lending_slip': list(lending_slip or []),
        'outside_summary': {
            'count': len(outside),
            'capacity': OUT_OF_RANGE_LIMIT,
            'numbers': outside,
            'contains_titles': False,
            'contains_inventory': False,
        },
        'coverage': {
            'catalog_total': len(saved['catalog_numbers']),
            'in_range': in_range_count,
            'outside': len(outside),
            'partition_complete': in_range_count + len(outside) == len(saved['catalog_numbers']),
            'empty_segment': in_range_count == 0,
        },
    }


def material_from_report(report) -> dict | None:
    """Rebuild the offline material from a stored inspection report."""
    if not isinstance(report, dict):
        return None
    fingerprint = report.get('fingerprint')
    if not isinstance(fingerprint, dict):
        material = report.get('offline_material')
        if isinstance(material, dict) and isinstance(material.get('fingerprint'), dict):
            fingerprint = material['fingerprint']
    if not isinstance(fingerprint, dict):
        return None
    slip = []
    for section in report.get('sections') or []:
        if isinstance(section, dict) and section.get('title') == '范围借阅单':
            slip = [row for row in section.get('rows') or [] if isinstance(row, dict)]
            break
    return build_material(fingerprint, slip)


def _raw_fingerprint(fingerprint) -> dict:
    if isinstance(fingerprint, dict) and isinstance(fingerprint.get('fingerprint'), dict):
        return fingerprint['fingerprint']
    return fingerprint


def _unwrap_fingerprint(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError('目录指纹须为对象')
    for key in ('fingerprint', '目录指纹'):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _read_fingerprint(fingerprint) -> dict:
    fingerprint = _unwrap_fingerprint(fingerprint)
    for key in ('page_capacity', 'catalog_numbers', 'in_range'):
        if key not in fingerprint:
            raise ValueError('目录指纹字段不完整')
    rng = fingerprint.get('range')
    if rng is not None and not isinstance(rng, dict):
        raise ValueError('目录指纹的借阅编号段格式有误')
    start = end = None
    if isinstance(rng, dict):
        start = _as_int(_pick(rng, START_KEYS), '目录指纹起始编号')
        end = _as_int(_pick(rng, END_KEYS), '目录指纹结束编号')
        if start > end:
            raise ValueError('目录指纹的起始编号须小于或等于结束编号')
    catalog = sorted({_as_int(value, '目录指纹编号')
                      for value in fingerprint['catalog_numbers']})
    if not isinstance(fingerprint['in_range'], list):
        raise ValueError('目录指纹的区间内登记须为数组')
    in_range = {}
    for item in fingerprint['in_range']:
        if not isinstance(item, dict):
            raise ValueError('目录指纹的区间内登记须为对象')
        number = _as_int(_pick(item, NUMBER_KEYS), '目录指纹编号')
        if number in in_range:
            raise ValueError(f'目录指纹的区间内登记编号 {number} 重复')
        title = _pick(item, TITLE_KEYS)
        copies = _pick(item, COPIES_KEYS)
        in_range[number] = {
            'number': number,
            'title': '' if title is None else str(title).strip(),
            'available_copies': _as_int(copies, '目录指纹可借份数'),
        }
    return {
        'digest': fingerprint.get('digest'),
        'page_capacity': _as_int(fingerprint['page_capacity'], '目录指纹页容量'),
        'start': start,
        'end': end,
        'catalog_numbers': catalog,
        'in_range': in_range,
    }


def reconcile(fingerprint, range_start=None, range_end=None, slip=None) -> dict:
    """Reconcile a saved fingerprint and borrowing-number segment with a slip.

    ``fingerprint`` may be the fingerprint itself or the whole material that
    nests it.  When ``range_start`` / ``range_end`` / ``slip`` are omitted the
    values recorded in the material (or the fingerprint's own interval and the
    material's slip) are used, which lets an empty segment prove its coverage.

    The result lists cross-batch (串批) rows, changed inventory (改库存) and
    missing scores (漏谱); a genuinely empty segment is proven complete against
    the full catalog number set held by the fingerprint.
    """
    material = None
    if isinstance(fingerprint, dict) and isinstance(fingerprint.get('fingerprint'), dict):
        material = fingerprint
    saved = _read_fingerprint(fingerprint)

    if range_start is None:
        range_start = (material or {}).get('segment', {})
        range_start = _pick(range_start, START_KEYS) if isinstance(range_start, dict) else None
    if range_end is None:
        range_end = (material or {}).get('segment', {})
        range_end = _pick(range_end, END_KEYS) if isinstance(range_end, dict) else None
    if range_start is None:
        range_start = saved['start']
    if range_end is None:
        range_end = saved['end']
    if range_start is None or range_end is None:
        raise ValueError('请提供借阅编号段（起始编号与结束编号）')
    start = _as_int(range_start, '起始曲谱编号')
    end = _as_int(range_end, '结束曲谱编号')
    if start > end:
        raise ValueError('起始曲谱编号须小于或等于结束曲谱编号')
    if slip is None:
        slip = material.get('lending_slip') if material else []
    if not isinstance(slip, list):
        raise ValueError('范围借阅单须为数组')

    catalog = saved['catalog_numbers']
    in_range = saved['in_range']
    issues: list[dict] = []

    def flag(kind, number, detail):
        issues.append({'类型': kind, '曲谱编号': number, '说明': detail})

    # 1. The fingerprint must still hash to its own digest.
    digest_ok = None
    if saved['digest'] and saved['start'] is not None:
        recomputed = _digest(_fingerprint_basis(
            saved['page_capacity'], saved['start'], saved['end'], catalog,
            list(in_range.values())))
        digest_ok = recomputed == saved['digest']
        if not digest_ok:
            flag('指纹校验', None, '目录指纹摘要与登记内容不符，指纹可能被篡改')

    # 2. The borrowing-number segment must match the fingerprint's interval.
    segment_ok = None
    if saved['start'] is not None:
        segment_ok = saved['start'] == start and saved['end'] == end
        if not segment_ok:
            flag('串批', None,
                 f'借阅编号段 {start} 至 {end} 与指纹登记的 {saved["start"]} 至 {saved["end"]} 不符（疑似串批）')

    # 3. Every in-range registration must belong to the catalog and the interval.
    catalog_set = set(catalog)
    for number in sorted(in_range):
        if number not in catalog_set:
            flag('指纹校验', number, '区间内登记编号不在指纹编号总表中')
        if not start <= number <= end:
            flag('指纹校验', number, f'区间内登记编号不在 {start} 至 {end} 区间内')

    # 4. Catalog numbers inside the segment must all be registered.
    catalog_segment = {number for number in catalog if start <= number <= end}
    omitted_from_fingerprint = catalog_segment - set(in_range)
    for number in sorted(omitted_from_fingerprint):
        flag('漏谱', number, '指纹编号总表含该区间内编号，但区间内登记缺失')

    # 5. Row-by-row reconciliation of the slip.
    seen: set[int] = set()
    for position, row in enumerate(slip, 1):
        if not isinstance(row, dict):
            flag('串批', None, f'借阅单第 {position} 行须为对象')
            continue
        try:
            number = _as_int(_pick(row, NUMBER_KEYS), '编号')
        except ValueError:
            flag('串批', None, f'借阅单第 {position} 行缺少有效曲谱编号')
            continue
        if number in seen:
            flag('串批', number, '借阅单重复登记该编号（疑似多批资料串批）')
            continue
        seen.add(number)
        registered = in_range.get(number)
        if registered is None:
            if start <= number <= end:
                flag('漏谱', number, '借阅单含本段编号，但指纹区间内登记缺失（漏谱）')
            else:
                flag('串批', number, '该编号不在本段区间内（疑似串批）')
            continue
        raw_title = _pick(row, TITLE_KEYS)
        if raw_title is not None and str(raw_title).strip() != registered['title']:
            flag('串批', number,
                 f'谱名「{str(raw_title).strip()}」与指纹「{registered["title"]}」不符（疑似串批）')
        raw_copies = _pick(row, COPIES_KEYS)
        if raw_copies is None:
            flag('改库存', number, '借阅单缺少可借份数，无法核对库存')
            continue
        try:
            copies = _as_int(raw_copies, '可借份数')
        except ValueError:
            flag('改库存', number, '借阅单可借份数不是整数，无法核对库存')
            continue
        if copies != registered['available_copies']:
            flag('改库存', number,
                 f'可借份数由指纹的 {registered["available_copies"]} 变为 {copies}')

    # 6. Registered scores that never appear in the slip.
    for number in sorted(set(in_range) - seen):
        flag('漏谱', number, '指纹登记的曲谱未出现在借阅单')

    cross_batch = sum(1 for item in issues if item['类型'] == '串批')
    changed = sum(1 for item in issues if item['类型'] == '改库存')
    missing = sum(1 for item in issues if item['类型'] == '漏谱')
    fingerprint_errors = sum(1 for item in issues if item['类型'] == '指纹校验')
    empty_segment = not catalog_segment
    outside_count = len(catalog) - len(catalog_segment)
    no_omission = not issues and seen == set(in_range) == catalog_segment

    if empty_segment and not issues:
        if catalog:
            proof = f'全目录 {len(catalog)} 条编号均在区间外且有据可查，未发现遗漏。'
        else:
            proof = '目录为空，区间内无可登记曲谱，未发现遗漏。'
        summary = (f'空段核验通过：编号 {start} 至 {end} 区间内登记 0 种曲谱，'
                   f'借阅单 {len(seen)} 行；{proof}')
    elif not issues:
        summary = (f'对账通过：编号 {start} 至 {end} 区间内登记 {len(in_range)} 种曲谱，'
                   f'借阅单 {len(seen)} 行，未发现串批、改库存或漏谱。')
    else:
        summary = (f'对账发现 {len(issues)} 处异常：串批 {cross_batch} 处、'
                   f'改库存 {changed} 处、漏谱 {missing} 处。')
        if fingerprint_errors:
            summary += f'另有 {fingerprint_errors} 处指纹校验异常。'
        if empty_segment:
            summary += '空段核验未能证明无遗漏。'

    checks = [
        {'检查项': '指纹摘要完整性',
         '判定': '通过' if digest_ok else ('未核验' if digest_ok is None else '失败'),
         '依据': ('目录指纹摘要与登记内容一致' if digest_ok
                  else ('未提供目录指纹摘要' if digest_ok is None
                        else '目录指纹摘要与登记内容不一致'))},
        {'检查项': '借阅编号段一致',
         '判定': '通过' if segment_ok else ('未核验' if segment_ok is None else '失败'),
         '依据': (f'对账编号段 {start} 至 {end}' + ('与指纹一致' if segment_ok
                   else ('指纹未登记编号段' if segment_ok is None else '与指纹不符')))},
        {'检查项': '编号覆盖',
         '判定': '通过' if not omitted_from_fingerprint else '失败',
         '依据': (f'指纹登记 {len(catalog)} 条编号，区间内 {len(catalog_segment)} 条，'
                  f'区间外 {outside_count} 条')},
        {'检查项': '逐条对账', '判定': '通过' if not issues else '失败',
         '依据': f'串批 {cross_batch} 处、改库存 {changed} 处、漏谱 {missing} 处'},
    ]
    if empty_segment:
        checks.append({
            '检查项': '空段无遗漏', '判定': '通过' if no_omission else '失败',
            '依据': (f'区间内登记 0 种，借阅单 {len(seen)} 行；'
                     f'全目录 {len(catalog)} 条编号均可核对'),
        })

    return {
        'summary': summary,
        'ok': not issues,
        'empty_segment': empty_segment,
        'counts': {
            '指纹区间内': len(in_range),
            '借阅单': len(seen),
            '串批': cross_batch,
            '改库存': changed,
            '漏谱': missing,
            '指纹校验': fingerprint_errors,
        },
        'issues': issues,
        'checks': checks,
        'coverage': {
            'catalog_numbers': len(catalog),
            'fingerprint_in_range': len(in_range),
            'slip_rows': len(seen),
            'outside_numbers': outside_count,
            'empty_segment': empty_segment,
            'no_omission': no_omission,
        },
    }
