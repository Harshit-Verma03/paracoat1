"""Small, dependency-free 2D loading diagrams for Streamlit review."""
from html import escape
import hashlib

import streamlit as st


PALETTE=["#17745c","#d5842c","#4968a8","#8b5ab0","#c65050","#4a8c9b","#879b37","#ad6e56"]


def _color(value):
    digest=hashlib.sha1(str(value).encode("utf-8")).digest()
    return PALETTE[digest[0]%len(PALETTE)]


def render_loading_layer(placements,vehicle,key):
    """Render the floor footprint of parcels whose bottom starts at one Z layer."""
    if not placements:
        st.info("No parcel placements are available for this vehicle.")
        return
    levels=sorted({round(float(row.get("Z_cm",0)),1) for row in placements})
    selected=st.selectbox("Loading layer · bottom height",levels,
        format_func=lambda value:"Floor level (Z 0 cm)" if value==0 else "Layer starting at Z {} cm".format(value),
        key="loading_layer_"+str(key))
    layer=[row for row in placements if round(float(row.get("Z_cm",0)),1)==selected]
    interior_length=float(vehicle.Interior_Length_cm)
    interior_width=float(vehicle.Interior_Width_cm)
    svg_width,svg_height=760,360
    margin_left,margin_top,margin_right,margin_bottom=55,18,20,42
    canvas_width=svg_width-margin_left-margin_right
    canvas_height=svg_height-margin_top-margin_bottom
    scale_x=canvas_width/interior_length
    scale_y=canvas_height/interior_width
    boxes=[]
    for item in layer:
        x=margin_left+float(item["X_cm"])*scale_x
        y=margin_top+float(item["Y_cm"])*scale_y
        width=max(1,float(item["Placed_Length_cm"])*scale_x)
        height=max(1,float(item["Placed_Width_cm"])*scale_y)
        color=_color(item.get("Order_ID",item.get("Parcel_ID","")))
        label=escape(str(item.get("Parcel_ID","Parcel")))
        unit=escape(str(item.get("Parcel_Unit_ID","")))
        boxes.append("<g><title>{} · {} · {} orientation · ({:.1f}, {:.1f}, {:.1f}) cm</title>"
            "<rect x='{:.1f}' y='{:.1f}' width='{:.1f}' height='{:.1f}' rx='3' fill='{}' fill-opacity='.88' stroke='white' stroke-width='2'/>"
            "<text x='{:.1f}' y='{:.1f}' text-anchor='middle' dominant-baseline='middle' fill='white' font-size='11' font-family='sans-serif'>{}</text></g>".format(
                unit,label,escape(str(item.get("Orientation",""))),float(item["X_cm"]),float(item["Y_cm"]),float(item["Z_cm"]),
                x,y,width,height,color,x+width/2,y+height/2,label[:18]))
    svg=("<svg viewBox='0 0 {} {}' role='img' aria-label='Top view of the selected vehicle cargo layer' "
        "style='width:100%;height:auto;background:#fbfcfb;border:1px solid #dfe8e2;border-radius:10px'>"
        "<rect x='{}' y='{}' width='{}' height='{}' fill='#f0f4f1' stroke='#718078' stroke-width='1.5'/>{}"
        "<text x='{}' y='{}' text-anchor='middle' fill='#53655b' font-size='12' font-family='sans-serif'>Cargo length · {} cm</text>"
        "<text x='14' y='{}' text-anchor='middle' fill='#53655b' font-size='12' font-family='sans-serif' transform='rotate(-90 14 {})'>Cargo width · {} cm</text>"
        "</svg>").format(svg_width,svg_height,margin_left,margin_top,canvas_width,canvas_height,"".join(boxes),
            margin_left+canvas_width/2,svg_height-10,interior_length,margin_top+canvas_height/2,margin_top+canvas_height/2,interior_width)
    st.markdown(svg,unsafe_allow_html=True)
    st.caption("Top view of parcels starting at Z={} cm. Box color groups shipments; hover a box for its unit, orientation, and position.".format(selected))


def render_loading_3d(placements,vehicle,key):
    """Show a lightweight interactive 3D box preview of the heuristic placements."""
    if not placements:
        st.info("No parcel placements are available for this vehicle.")
        return
    import plotly.graph_objects as go

    xs=[];ys=[];zs=[];ii=[];jj=[];kk=[];facecolors=[];hover=[]
    faces=((0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
           (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7))
    for item in placements:
        x=float(item["X_cm"]);y=float(item["Y_cm"]);z=float(item["Z_cm"])
        length=float(item["Placed_Length_cm"]);width=float(item["Placed_Width_cm"]);height=float(item["Placed_Height_cm"])
        offset=len(xs)
        xs.extend((x,x+length,x+length,x,x,x+length,x+length,x))
        ys.extend((y,y,y+width,y+width,y,y,y+width,y+width))
        zs.extend((z,z,z,z,z+height,z+height,z+height,z+height))
        for a,b,c in faces:
            ii.append(offset+a);jj.append(offset+b);kk.append(offset+c)
            facecolors.append(_color(item.get("Order_ID",item.get("Parcel_ID",""))))
        tip="{} · {} · {} · {} · {} × {} × {} cm · position ({}, {}, {}) cm".format(
            escape(str(item.get("Parcel_Unit_ID",""))),escape(str(item.get("Order_ID",""))),
            escape(str(item.get("Parcel_ID",""))),escape(str(item.get("Orientation",""))),
            length,width,height,x,y,z)
        hover.extend([tip]*8)
    figure=go.Figure()
    figure.add_trace(go.Mesh3d(
        x=xs,y=ys,z=zs,i=ii,j=jj,k=kk,facecolor=facecolors,
        flatshading=True,opacity=.82,hovertext=hover,hoverinfo="text",name="Parcel units",
        lighting={"ambient":.8,"diffuse":.6,"roughness":.8,"specular":.15},
        lightposition={"x":100,"y":-100,"z":180},
    ))
    length=float(vehicle.Interior_Length_cm);width=float(vehicle.Interior_Width_cm);height=float(vehicle.Interior_Height_cm)
    corners=((0,0,0),(length,0,0),(length,width,0),(0,width,0),
             (0,0,height),(length,0,height),(length,width,height),(0,width,height))
    edges=((0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7))
    edge_x=[];edge_y=[];edge_z=[]
    for start,end in edges:
        for corner in (corners[start],corners[end]):
            edge_x.append(corner[0]);edge_y.append(corner[1]);edge_z.append(corner[2])
        edge_x.append(None);edge_y.append(None);edge_z.append(None)
    figure.add_trace(go.Scatter3d(x=edge_x,y=edge_y,z=edge_z,mode="lines",
        line={"color":"#263b32","width":3},hoverinfo="skip",name="Vehicle interior"))
    scale=max(length,width,height)
    figure.update_layout(
        height=500,margin={"l":0,"r":0,"t":10,"b":0},showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
        scene={
            "xaxis":{"title":"Length · cm","range":[0,length],"backgroundcolor":"#f5f8f6","gridcolor":"#d8e1db"},
            "yaxis":{"title":"Width · cm","range":[0,width],"backgroundcolor":"#f5f8f6","gridcolor":"#d8e1db"},
            "zaxis":{"title":"Height · cm","range":[0,height],"backgroundcolor":"#f5f8f6","gridcolor":"#d8e1db"},
            "aspectmode":"manual","aspectratio":{"x":length/scale,"y":width/scale,"z":height/scale},
            "camera":{"eye":{"x":1.7,"y":-1.8,"z":1.35}},
        },
        uirevision="cargo-{}".format(key),
    )
    st.plotly_chart(figure,use_container_width=True,key="loading_3d_"+str(key),config={"displaylogo":False,"scrollZoom":True})
    st.caption("Rotate or zoom to inspect the estimated 3D positions. Colors group shipments; hover a box for its parcel unit, orientation, size, and coordinates. This is a review aid, not a loading-safety or structural analysis.")
