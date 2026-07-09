import time
import io

import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime

st.title("ノードイベントビューア")

uploaded_file = st.file_uploader("ログファイルをアップロード", type=["csv", "txt", "tsv", "log"])


def parse_time_to_seconds(t: str) -> float:
    """hh:mm:ss.SSS 形式の文字列を秒数(float)に変換する"""
    t = t.strip()
    dt = datetime.strptime(t, "%H:%M:%S.%f")
    return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1_000_000


if uploaded_file is not None:
    # 区切り文字の自動判定(sep=None)は、桁揃え用の余分なスペースなどがあると
    # 誤判定して列がずれることがあるため、候補を順に試して
    # 想定の5列(時刻,ノードID,セグメント,コード値,creq/pres)に一致するものを採用する
    raw_text = uploaded_file.getvalue().decode("utf-8", errors="replace")
    COLUMN_NAMES = ["time_str", "node_id", "segment", "code", "req_type"]
    SEP_CANDIDATES = [",", "\t", r"\s+"]

    df = None
    for sep in SEP_CANDIDATES:
        try:
            candidate = pd.read_csv(
                io.StringIO(raw_text),
                sep=sep,
                engine="python",
                header=None,
                names=COLUMN_NAMES,
            )
        except Exception:
            continue
        # 列数が一致していても、実際には分割できておらず欠損値で埋められている
        # だけのケースがあるため、NaNが含まれていないことも確認する
        if candidate.shape[1] == len(COLUMN_NAMES) and not candidate.isna().any().any():
            df = candidate
            break

    if df is None:
        st.error(
            "列数が想定(5列: 時刻,ノードID,セグメント,コード値,creq/pres)と一致せず、"
            "区切り文字の判定に失敗しました。ファイル形式をご確認ください。"
        )
        st.stop()

    # 文字列の前後空白を除去
    df["time_str"] = df["time_str"].astype(str).str.strip()
    df["node_id"] = df["node_id"].astype(str).str.strip()
    df["segment"] = df["segment"].astype(str).str.strip()
    df["code"] = pd.to_numeric(df["code"], errors="coerce")
    # 5列目は preq(要求)/pres(応答受信) を区別するために使用する。
    df["req_type"] = df["req_type"].astype(str).str.strip()

    try:
        df["abs_sec"] = df["time_str"].apply(parse_time_to_seconds)
    except ValueError as e:
        st.error(f"時刻のパースに失敗しました。hh:mm:ss.SSS 形式か確認してください: {e}")
        st.stop()

    df = df.sort_values("abs_sec").reset_index(drop=True)

    # 最初のエントリを 0 とした相対秒に変換
    t0 = df["abs_sec"].iloc[0]
    df["rel_sec"] = df["abs_sec"] - t0

    st.write(f"データ件数: {len(df)} 行 / 開始時刻(0秒): {df['time_str'].iloc[0]}")

    max_sec = float(df["rel_sec"].max())

    # --- 自動再生用の状態を初期化 ---
    if "selected_sec" not in st.session_state:
        st.session_state.selected_sec = 0.0
    if "playing" not in st.session_state:
        st.session_state.playing = False

    col_play, col_step, col_speed = st.columns([1, 1.2, 2])
    with col_play:
        st.write("")  # ボタンの縦位置をラベルに合わせる
        play_label = "⏸ 停止" if st.session_state.playing else "▶ 自動再生"
        if st.button(play_label, use_container_width=True):
            st.session_state.playing = not st.session_state.playing
            # 末端で停止していた場合、再生開始時に先頭へ戻す
            if st.session_state.playing and st.session_state.selected_sec >= max_sec:
                st.session_state.selected_sec = 0.0
    with col_step:
        step_choice = st.selectbox(
            "ステップ幅",
            options=[1.0, 10.0],
            format_func=lambda x: f"{x:.0f} 秒ごと",
        )
    with col_speed:
        interval = st.slider(
            "更新間隔(実時間・秒)", min_value=0.1, max_value=2.0, value=0.5, step=0.1
        )

    # 自動再生中は、スライダーを生成する前に時刻を1ステップ進める
    if st.session_state.playing:
        next_t = st.session_state.selected_sec + step_choice
        if next_t >= max_sec:
            st.session_state.selected_sec = max_sec
            st.session_state.playing = False
        else:
            st.session_state.selected_sec = next_t

    selected_sec = st.slider(
        "時刻(先頭からの経過秒数)",
        min_value=0.0,
        max_value=max_sec if max_sec > 0 else 1.0,
        step=0.001,
        format="%.3f 秒",
        key="selected_sec",
    )

    st.caption(f"選択中の時刻: {selected_sec:.3f} 秒 (前後1秒の範囲を表示)")

    window = 1.0
    mask = (df["rel_sec"] >= selected_sec - window) & (df["rel_sec"] <= selected_sec + window)
    nearby = df[mask].copy()
    nearby["|Δt| (秒)"] = (nearby["rel_sec"] - selected_sec).abs().round(3)
    nearby = nearby.sort_values("|Δt| (秒)")

    # --- ノードID 0〜49 を横に並べたグリッド表示 ---
    NODE_MIN, NODE_MAX = 0, 49

    # コード値 -> 色 のマッピング(bg:塗りつぶし用RGB, border:枠線色)
    CODE_COLORS = {
        720: {"rgb": (37, 99, 235), "border": "#1d4ed8", "label": "青"},   # 青
        480: {"rgb": (249, 115, 22), "border": "#c2410c", "label": "オレンジ"},  # オレンジ
        360: {"rgb": (22, 163, 74), "border": "#15803d", "label": "緑"},   # 緑
        240: {"rgb": (220, 38, 38), "border": "#b91c1c", "label": "赤"},   # 赤
    }
    UNKNOWN_CODE_STYLE = {"rgb": (100, 116, 139), "border": "#334155", "label": "その他"}
    NO_EVENT_STYLE = {"bg": "#f1f5f9", "border": "#cbd5e1"}

    # イベントありノードごとに、選択時刻に最も近いイベントの行を求める(色・濃淡の決定に使う)
    nearby_numeric = nearby.copy()
    nearby_numeric["node_num"] = pd.to_numeric(nearby_numeric["node_id"], errors="coerce")
    nearby_numeric = nearby_numeric.dropna(subset=["node_num"])

    nearest_per_node = {}
    if not nearby_numeric.empty:
        idx = nearby_numeric.groupby("node_num")["|Δt| (秒)"].idxmin()
        nearest_rows = nearby_numeric.loc[idx].set_index("node_num")
        nearest_per_node = nearest_rows[["|Δt| (秒)", "segment", "code"]].to_dict(orient="index")

    def resolve_box_style(info):
        """直近イベント情報(info)から (背景色, 枠線色, 文字色, 表示テキスト) を求める"""
        if info is None:
            return NO_EVENT_STYLE["bg"], NO_EVENT_STYLE["border"], "#94a3b8", ""
        dt = info["|Δt| (秒)"]
        code = info["code"]
        segment_text = info["segment"]
        style = CODE_COLORS.get(int(code), UNKNOWN_CODE_STYLE) if pd.notna(code) else UNKNOWN_CODE_STYLE
        # 選択時刻に近いほど濃く、遠い(最大±1秒)ほど薄く
        intensity = 1.0 - min(dt / window, 1.0)
        alpha = 0.35 + 0.65 * intensity
        r, g, b = style["rgb"]
        bg = f"rgba({r},{g},{b},{alpha:.2f})"
        return bg, style["border"], "#ffffff", segment_text

    cell_w = 38  # 1ノードあたりの幅(px)。3桁のセグメント番号が収まるよう拡大
    cells_html = []
    for node in range(NODE_MIN, NODE_MAX + 1):
        info = nearest_per_node.get(float(node))
        bg, border, box_text_color, segment_text = resolve_box_style(info)

        cell_style = (
            f"display:flex;flex-direction:column;align-items:center;"
            f"width:{cell_w}px;flex:0 0 {cell_w}px;"
        )
        box_style = (
            f"width:{cell_w - 4}px;height:{cell_w - 4}px;"
            f"background:{bg};border:1px solid {border};border-radius:4px;"
            f"display:flex;align-items:center;justify-content:center;"
            f"font-size:11px;font-weight:700;color:{box_text_color};"
            f"letter-spacing:-0.5px;overflow:hidden;white-space:nowrap;"
        )
        label_style = "font-size:9px;color:#475569;margin-top:2px;"

        cells_html.append(
            f'<div style="{cell_style}">'
            f'<div style="{box_style}">{segment_text}</div>'
            f'<div style="{label_style}">{node}</div>'
            f"</div>"
        )

    # 各行の先頭にインデントを入れない(Markdownがコードブロックと誤認識するのを防ぐため)
    # 横幅を820pxに収め、収まりきらない分は自動的に折り返して複数行にする
    grid_html = (
        '<div style="display:flex;flex-direction:row;flex-wrap:wrap;'
        'justify-content:center;gap:4px;max-width:min(820px, 95vw);margin:0 auto;">'
        + "".join(cells_html)
        + "</div>"
    )

    # --- 上位ノード(node_id = -1)。要求(preq)と応答受信(pres)を別ボックスで表示 ---
    top_rows = nearby_numeric[nearby_numeric["node_num"] == -1.0]

    def nearest_info_for(req_type_value):
        subset = top_rows[top_rows["req_type"] == req_type_value]
        if subset.empty:
            return None
        idx = subset["|Δt| (秒)"].idxmin()
        row = subset.loc[idx]
        return {"|Δt| (秒)": row["|Δt| (秒)"], "segment": row["segment"], "code": row["code"]}

    preq_info = nearest_info_for("preq")
    pres_info = nearest_info_for("pres")

    top_cell_w = 46

    def render_top_box(info, label):
        bg, border, text_color, segment_text = resolve_box_style(info)
        box_style = (
            f"width:{top_cell_w - 4}px;height:{top_cell_w - 4}px;"
            f"background:{bg};border:2px solid {border};border-radius:6px;"
            f"display:flex;align-items:center;justify-content:center;"
            f"font-size:13px;font-weight:700;color:{text_color};"
            f"letter-spacing:-0.5px;overflow:hidden;white-space:nowrap;"
            f"box-shadow:0 1px 3px rgba(0,0,0,0.15);"
        )
        return (
            '<div style="display:flex;flex-direction:column;align-items:center;'
            'justify-content:center;">'
            f'<div style="{box_style}">{segment_text}</div>'
            f'<div style="font-size:9px;color:#475569;margin-top:2px;">{label}</div>'
            "</div>"
        )

    top_box_html = (
        '<div style="display:flex;flex-direction:row;justify-content:center;'
        'gap:24px;margin-bottom:10px;">'
        + render_top_box(preq_info, "上位ノード：要求 (preq)")
        + render_top_box(pres_info, "上位ノード：応答受信 (pres)")
        + "</div>"
    )

    # 凡例
    legend_items = []
    for code, style in CODE_COLORS.items():
        r, g, b = style["rgb"]
        legend_items.append(
            f'<div style="display:flex;align-items:center;gap:4px;margin-right:14px;">'
            f'<div style="width:14px;height:14px;background:rgb({r},{g},{b});'
            f'border:1px solid {style["border"]};border-radius:3px;"></div>'
            f'<span style="font-size:12px;color:#334155;">{code}p（{style["label"]}）</span>'
            f"</div>"
        )
    legend_html = (
        '<div style="display:flex;flex-wrap:wrap;margin-top:6px;margin-bottom:10px;">'
        + "".join(legend_items)
        + "</div>"
    )

    st.subheader(f"ノード状態 (ID {NODE_MIN}〜{NODE_MAX} / 選択時刻 ±{window:.0f} 秒でイベントありのノードが着色)")
    st.markdown(top_box_html, unsafe_allow_html=True)
    st.markdown(grid_html, unsafe_allow_html=True)
    st.markdown(legend_html, unsafe_allow_html=True)

    # --- ノードごとのイベント発生状況グラフ(横軸:時間, 縦軸:ノードID, 色:コード値) ---
    st.subheader("ノードごとのイベント発生状況")

    def rgb_to_hex(rgb):
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    # ノードIDごとに (時刻, コード値, セグメント番号) のリストを持たせ、時刻でソートしておく
    # (セグメント番号は現時点ではグラフに使用しない)
    df_numeric = df.copy()
    df_numeric["node_num"] = pd.to_numeric(df_numeric["node_id"], errors="coerce")
    df_numeric = df_numeric.dropna(subset=["node_num"])

    events_by_node = {}
    for node_num, group in df_numeric.groupby("node_num"):
        if node_num == -1.0:
            continue  # 上位ノードは専用グラフに分離して表示する
        events_by_node[node_num] = sorted(
            zip(group["rel_sec"], group["code"], group["segment"]),
            key=lambda item: item[0],
        )

    # 「指定時刻より前」だが、指定時刻の前後1秒(ウィンドウ)分は含めるため、
    # しきい値は 選択時刻 + ウィンドウ(1秒) とする
    chart_threshold = selected_sec + window
    chart_rows = []
    for node_num, events in events_by_node.items():
        for t, code, _segment in events:
            if t <= chart_threshold:
                style = (
                    CODE_COLORS.get(int(code), UNKNOWN_CODE_STYLE)
                    if pd.notna(code)
                    else UNKNOWN_CODE_STYLE
                )
                chart_rows.append(
                    {
                        "経過秒数": t,
                        "ノードID": node_num,
                        "color": rgb_to_hex(style["rgb"]),
                    }
                )

    if chart_rows:
        chart_df = pd.DataFrame(chart_rows)

        # st.scatter_chart はマーカーサイズを細かく制御できず、
        # 点が密集すると後から描画された色が下の色を覆い隠してしまう(オーバープロット)。
        # altair_chart を使い、マーカーを小さく・半透明にすることでこれを緩和する。
        chart = (
            alt.Chart(chart_df)
            .mark_circle(size=16, opacity=0.75)
            .encode(
                x=alt.X("経過秒数:Q", title="経過秒数"),
                y=alt.Y("ノードID:Q", title="ノードID"),
                color=alt.Color("color:N", scale=None, legend=None),
                tooltip=["経過秒数", "ノードID"],
            )
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "点の色は凡例（720p=青 / 480p=オレンジ / 360p=緑 / 240p=赤）に対応しています。"
            "点が密集する場合は重なって見えることがあります。"
        )

    else:
        st.info("表示できるイベントがまだありません。")

    # --- 上位ノード(node_id = -1)とのやり取りグラフ(横軸:時間, 縦軸:セグメント番号) ---
    st.subheader("上位ノードとのイベント発生状況（要求 / 応答受信）")

    SEGMENT_Y_OFFSET = 0.15  # 要求(上)と応答受信(下)を少しだけ縦にずらして重ならないようにする

    top_numeric = df_numeric[df_numeric["node_num"] == -1.0].copy()
    top_numeric["segment_num"] = pd.to_numeric(top_numeric["segment"], errors="coerce")
    top_numeric = top_numeric.dropna(subset=["segment_num"])
    top_numeric = top_numeric[top_numeric["rel_sec"] <= chart_threshold]

    def build_top_rows(subset_df, y_offset):
        rows = []
        for _, row in subset_df.iterrows():
            code_val = row["code"]
            style = (
                CODE_COLORS.get(int(code_val), UNKNOWN_CODE_STYLE)
                if pd.notna(code_val)
                else UNKNOWN_CODE_STYLE
            )
            rows.append(
                {
                    "経過秒数": row["rel_sec"],
                    "y位置": row["segment_num"] + y_offset,
                    "セグメント番号": row["segment_num"],
                    "color": rgb_to_hex(style["rgb"]),
                }
            )
        return pd.DataFrame(rows)

    preq_df = build_top_rows(top_numeric[top_numeric["req_type"] == "preq"], SEGMENT_Y_OFFSET)
    pres_df = build_top_rows(top_numeric[top_numeric["req_type"] == "pres"], -SEGMENT_Y_OFFSET)

    if not preq_df.empty or not pres_df.empty:
        layers = []
        if not preq_df.empty:
            # 要求(preq): 白抜き(open)の○
            layers.append(
                alt.Chart(preq_df)
                .mark_point(shape="circle", filled=False, size=16, opacity=0.85, strokeWidth=1.3)
                .encode(
                    x=alt.X("経過秒数:Q", title="経過秒数"),
                    y=alt.Y("y位置:Q", title="セグメント番号"),
                    color=alt.Color("color:N", scale=None, legend=None),
                    tooltip=["経過秒数", "セグメント番号"],
                )
            )
        if not pres_df.empty:
            # 応答受信(pres): 塗りつぶし(closed)の●
            layers.append(
                alt.Chart(pres_df)
                .mark_circle(size=16, opacity=0.85)
                .encode(
                    x=alt.X("経過秒数:Q", title="経過秒数"),
                    y=alt.Y("y位置:Q", title="セグメント番号"),
                    color=alt.Color("color:N", scale=None, legend=None),
                    tooltip=["経過秒数", "セグメント番号"],
                )
            )
        top_chart = layers[0]
        for layer in layers[1:]:
            top_chart = top_chart + layer
        st.altair_chart(top_chart, use_container_width=True)
        st.caption(
            "○（白抜き）=要求(preq)、●（塗りつぶし）=応答受信(pres)。"
            "各セグメント番号の少し上に要求、少し下に応答受信を表示しています。"
            "色は他のグラフと同じ凡例（720p=青 / 480p=オレンジ / 360p=緑 / 240p=赤）です。"
        )
    else:
        st.info("上位ノードのイベントはまだありません。")

    st.subheader(f"選択時刻 ±{window:.0f} 秒 のイベント ({len(nearby)} 件)")
    st.dataframe(
        nearby[["time_str", "rel_sec", "node_id", "segment", "code", "req_type", "|Δt| (秒)"]].rename(
            columns={
                "time_str": "時刻",
                "rel_sec": "経過秒数",
                "node_id": "ノードID",
                "segment": "セグメント番号",
                "code": "コード値",
                "req_type": "種別",
            }
        ),
        use_container_width=True,
    )

    # 自動再生中は指定間隔だけ待ってから再実行し、時刻を1ステップ進める
    if st.session_state.playing:
        time.sleep(interval)
        st.rerun()

else:
    st.info(
        "ログファイルをアップロードしてください"
        "（形式: 時刻,ノードID,セグメント番号,コード値,preq/pres を1行ずつ。"
        "ノードID=-1は上位ノードとして扱われ、preq=要求／pres=応答受信 で区別されます）"
    )
