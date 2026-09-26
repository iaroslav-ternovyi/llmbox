from .models import Parcel


def pack(items, boxes):
    units = []
    for it in items:
        units += [it] * it.qty
    units.sort(key=lambda u: (-u.volume, u.sku))  #@MUT p-sort: units.sort(key=lambda u: (u.volume, u.sku))
    by_size = sorted(boxes, key=lambda b: (b.volume, b.max_g))  #@MUT p-smallest: by_size = list(boxes)[::-1]
    open_parcels = []  # [box, skus, used_volume, used_weight]
    for u in units:
        placed = False
        for p in open_parcels:
            box = p[0]
            if p[2] + u.volume <= box.volume and p[3] + u.weight_g <= box.max_g:  #@MUT p-weight: if p[2] + u.volume <= box.volume:
                p[1].append(u.sku)
                p[2] += u.volume
                p[3] += u.weight_g
                placed = True
                break
        if not placed:
            box = next((b for b in by_size if u.volume <= b.volume and u.weight_g <= b.max_g), None)
            if box is None:
                raise ValueError(f"{u.sku} does not fit any box")
            open_parcels.append([box, [u.sku], u.volume, u.weight_g])
    return [Parcel(b.name, skus, w + b.tare_g, b.l, b.w, b.h) for b, skus, _v, w in open_parcels]  #@MUT p-tare: return [Parcel(b.name, skus, w, b.l, b.w, b.h) for b, skus, _v, w in open_parcels]
