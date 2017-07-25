
# coding: utf-8
import sys
import json
import fiona
from shapely.geometry import (
    asShape,
    mapping,
    box,
    MultiLineString,
    MultiPoint,
    LineString,
    MultiPolygon,
)
from shapely.ops import cascaded_union, transform, linemerge
from functools import partial
import pyproj

import logging
log = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

def transform_bbox(transf, bbox):
    xs, ys = transf([bbox[0], bbox[0], bbox[2], bbox[2]], [bbox[1], bbox[3], bbox[1], bbox[3]])
    return min(xs), min(ys), max(xs), max(ys)

def as_multipolygon(p):
    if p.type == 'MultiPolygon':
        return p
    return MultiPolygon([p])

def as_multilinestring(l):
    if l.type == 'MultiLineString':
        return l
    return MultiLineString([l])

def as_multipoint(l):
    if l.type == 'MultiPoint':
        return l
    return MultiPoint([l])


def to_lines(poly):
    if poly.type == 'Polygon':
        geoms = [poly]
    else:
        geoms = poly.geoms
    lines = []
    for g in geoms:
        lines.append(g.exterior)
        lines.extend(g.interiors)
    return as_multilinestring(cascaded_union(lines))


def intersection_points(a, b):
    points = a.intersection(b)
    if points.type == 'Point':
        return [points]
    return points

def filter_small_segments(ml, min_length):
    parts = []
    for g in ml.geoms:
        if g.length > min_length:
            parts.append(g)
            continue
    return MultiLineString(parts)

def truncate(xs, ys, zs=None):
    if isinstance(xs, float):
        return int(xs*1e6)/1e6, int(ys*1e6)/1e6
    xs = [int(x*1e6)/1e6 for x in xs]
    ys = [int(y*1e6)/1e6 for y in ys]
    return xs, ys


def main():
    import optparse
    parser = optparse.OptionParser()
    parser.add_option('--clip-file')
    parser.add_option('--src-file')
    parser.add_option('--result-file')

    parser.add_option('--clip-srs')
    parser.add_option('--src-srs')

    parser.add_option('--clip-buffer', type=int, default=100)
    parser.add_option('--min-segment-length', type=int, default=2000)

    opts, args = parser.parse_args()

    if not(all([
        opts.src_file,
        opts.clip_file,
        opts.result_file,
        opts.clip_srs,
        opts.src_srs,
    ])):
        parser.print_help()
        sys.exit(1)

    clip_fname = opts.clip_file
    src_fname = opts.src_file
    result_fname = opts.result_file

    clip_buffer = opts.clip_buffer
    min_clip_length = clip_buffer * 2
    min_segment_length = opts.min_segment_length

    proj_clip_to_src = partial(
        pyproj.transform,
        pyproj.Proj(init=opts.clip_srs),
        pyproj.Proj(init=opts.src_srs))

    proj_src_to_clip = partial(
        pyproj.transform,
        pyproj.Proj(init=opts.src_srs),
        pyproj.Proj(init=opts.clip_srs))

    proj_src_to_wgs = partial(
        pyproj.transform,
        pyproj.Proj(init=opts.src_srs),
        pyproj.Proj(init='epsg:4326'))

    result_features = []

    with fiona.open(clip_fname) as clipsrc:
        with fiona.open(src_fname) as source:
            for src_feature in source:
                log.debug('processing %r', src_feature['properties'])
                boundary = as_multipolygon(asShape(src_feature['geometry']))
                boundary_lines = to_lines(boundary)

                bbox = box(*boundary.bounds).buffer(clip_buffer*2, 1).bounds
                bbox_clip = transform_bbox(proj_src_to_clip, bbox)

                clip_features = clipsrc.items(bbox=bbox_clip)
                clip_geom = as_multipolygon(cascaded_union([asShape(f['geometry']) for i, f in clip_features]))
                clip_geom = transform(proj_clip_to_src, clip_geom)

                if clip_geom.is_empty:
                    result_features.append({
                        'type': 'Feature',
                        'properties': src_feature['properties'],
                        'geometry': mapping(transform(truncate, transform(proj_src_to_wgs, boundary_lines))),
                    })
                    continue

                # Buffer the clipping geometry
                clip_geom_buffered = clip_geom.buffer(clip_buffer)

                # Shortcut if there is no intersection.
                if not clip_geom_buffered.intersects(boundary):
                    result_features.append({
                        'type': 'Feature',
                        'properties': src_feature['properties'],
                        'geometry': mapping(transform(truncate, transform(proj_src_to_wgs, boundary_lines))),
                    })
                    continue

                # Remove all parts within buffered clipping geometry.
                # This is the main part of the final boundary geometry.
                clipped = boundary_lines.difference(clip_geom_buffered)


                if clipped.is_empty:
                    # Completely inside clip_geom.
                    continue

                clipped = as_multilinestring(clipped)

                # Get linestrings that were removed and add them back to the clipped lines if
                # they are smaller then min_clip_length
                removed = clip_geom_buffered.intersection(boundary_lines)
                removed = as_multilinestring(removed)
                # Intersection can return a MultiLineString with connected LineString, we don't want to
                # filter these out -> linemerge.
                removed = linemerge(removed)

                add_back = []
                for removed in as_multilinestring(removed):
                    if removed.length < min_clip_length:
                        add_back.append(removed)
                clipped = as_multilinestring(linemerge(MultiLineString(list(clipped.geoms) + add_back)))


                # We want to extend the clipped line back to the actual clip_line (from @ to %)
                #
                # - Collect all clip_points (@)
                # - For each clip_point
                #   - Search for nearest clip_line
                #   - Get nearest point (%) on clip_line to @
                #   - Create LineString for @ to %
                #
                #   ─────┐ clip_line
                #        └─────┐
                #              └─────┐
                #                    %─────┐
                #                          └────┐
                # ─ ─ ─ ┐                       └────┐
                #        ─ ─ ─ ┐                     └────┐
                #               ─ @ ─                     └────
                #                ╱   └ ─ ─ ┐
                #               ╱           ─ ─ ─
                #              ╱                 └ ─ ─ ┐
                #             ╱                         ─ ─ ─  clip_geom_buffered_lines
                #            ╱
                #           ╱  clipped

                clip_geom_buffered_lines = to_lines(clip_geom_buffered)
                # We need to intersect with full lines and not the clipped lines to get all
                # clip_points.
                clip_points = intersection_points(clip_geom_buffered_lines, boundary_lines)

                # Remove small segments, i.e. when a small part is further away
                # from clip_geom then the buffer size
                clipped = filter_small_segments(clipped, min_segment_length)

                if clipped.is_empty:
                    continue

                # Filter clip_points so that they all intersect with our clipped lines.
                intersect_points = []
                for cp in clip_points:
                    if cp.intersects(clipped):
                        intersect_points.append(cp)
                if not intersect_points:
                    clip_points = []
                else:
                    clip_points = as_multipoint(cascaded_union(intersect_points))

                clip_lines = to_lines(clip_geom)
                segs = []
                for cp in clip_points:
                    w_seg = []
                    min_seg = None
                    min_seg_distance = 1e99
                    for l in clip_lines.geoms:
                        dist = l.distance(cp)
                        if dist < min_seg_distance:
                            min_seg = l
                            min_seg_distance = dist

                    assert min_seg is not None
                    if min_seg_distance > clip_buffer*1.1:
                        log.warn('found no clip line near %s, minimum distance is %.2f', cp, min_seg_distance)
                    else:
                        # the distance along min_seg to the point nearest the clip point
                        m = min_seg.project(cp)
                        # the point on min_seg at distance along the geometry
                        p = min_seg.interpolate(m)
                        segs.append(LineString([cp, p]))

                # Use linemerge to combine created segments with clipped lines.
                combined = as_multilinestring(linemerge(MultiLineString(segs + list(as_multilinestring(clipped).geoms))))
                old_length = combined.length
                # Filter out small linestrings that slightly crossed our clip geometry.
                combined_filtered = filter_small_segments(combined, min_segment_length)
                new_length = combined_filtered.length
                if old_length != new_length:
                    log.debug('removed lines, from %.2f to %.2f', old_length, new_length)


                # out_geoms = [combined_filtered]
                out_geoms = combined_filtered.geoms
                for g in out_geoms:
                    result_features.append({
                        'type': 'Feature',
                        'properties': src_feature['properties'],
                        'geometry': mapping(transform(truncate, transform(proj_src_to_wgs, g))),
                    })

    result = {'type': 'FeatureCollection', 'features': result_features}

    with open(result_fname, 'w') as f:
        json.dump(result, fp=f)

if __name__ == '__main__':
    main()
