import os
import pya


def export_selected_polygon():
    app = pya.Application.instance()
    mw = app.main_window()
    view = mw.current_view() if mw else None
    if view is None:
        pya.MessageBox.warning("Export", "No active view.", pya.MessageBox.Ok)
        return

    cv = view.active_cellview()
    if cv is None or not cv.is_valid():
        pya.MessageBox.warning("Export", "No active cellview.", pya.MessageBox.Ok)
        return

    layout = cv.layout()
    gds_path = cv.filename()
    if not gds_path:
        pya.MessageBox.warning(
            "Export", "Please save the GDS first.", pya.MessageBox.Ok
        )
        return

    selection = view.object_selection
    if not selection:
        pya.MessageBox.warning("Export", "No objects selected.", pya.MessageBox.Ok)
        return

    line = None
    for sel in selection:
        try:
            shape = sel.shape
        except AttributeError:
            continue
        if shape is None or shape.is_null():
            continue

        trans = getattr(sel, "trans", pya.Trans())
        if callable(trans):
            trans = trans()
        if not isinstance(trans, (pya.Trans, pya.ICplxTrans, pya.CplxTrans)):
            trans = pya.Trans()

        if shape.is_polygon():
            poly = shape.polygon.transformed(trans)
        elif shape.is_box():
            poly = pya.Polygon(shape.box).transformed(trans)
        else:
            continue
        try:
            layer_index = sel.layer
        except AttributeError:
            continue
        layer_info = layout.get_info(layer_index)
        layer_str = f"{layer_info.layer}/{layer_info.datatype}"

        coords = []
        for pt in poly.each_point_hull():
            coords.append(f"{pt.x}_{pt.y}")

        line = f"{layer_str}@" + "_".join(coords)
        break

    if line is None:
        pya.MessageBox.warning("Export", "No polygon selected.", pya.MessageBox.Ok)
        return

    out_dir = os.path.dirname(gds_path)
    cell_name = cv.cell.name
    out_path = os.path.join(out_dir, f"{cell_name}_selected_polygons.txt")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(line + "\n")

    pya.MessageBox.info("Export", f"Saved to:\n{out_path}", pya.MessageBox.Ok)


export_selected_polygon()
