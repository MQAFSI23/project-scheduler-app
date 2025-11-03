import plotly.graph_objects as go
from datetime import datetime
import plotly.express as px
import pandas as pd
import graphviz
import tempfile
import gradio as gr

def calculate_cpm(df, start_date):
    """
    Menghitung CPM menggunakan explicit topological sort (Kahn). 
    Mengembalikan: (df_display, critical_path, df_with_datetimes)
    Modifikasi: st.error diganti dengan raise gr.Error
    """
    # Pastikan input
    df = df.copy()
    df['Duration (Days)'] = pd.to_numeric(df['Duration (Days)'], errors='coerce').fillna(1).astype(int)
    df['Dependencies'] = df['Dependencies'].fillna('')

    # Normalisasi nama Aktivitas (strip) dan Dependensi menjadi list (strip setiap)
    df['Activity'] = df['Activity'].astype(str).str.strip()
    def parse_deps(x):
        if pd.isna(x) or str(x).strip() == '':
            return []
        # split dengan koma dan strip setiap token, abaikan yang kosong
        parts = [p.strip() for p in str(x).split(',')]
        return [p for p in parts if p != '']
    df['Dependencies'] = df['Dependencies'].apply(parse_deps)

    # Validasi dasar: nama aktivitas unik
    if df['Activity'].duplicated().any():
        dupes = df.loc[df['Activity'].duplicated(keep=False), 'Activity'].unique().tolist()
        raise gr.Error(f"Duplicate Activity ID found: {', '.join(map(str, dupes))}. Activity names must be unique.")

    activities = df['Activity'].tolist()
    # Validasi dependensi merujuk ke aktivitas yang ada
    all_deps = set(sum(df['Dependencies'].tolist(), []))
    bad_refs = [d for d in all_deps if d not in activities]
    if bad_refs:
        raise gr.Error(f"Dependency refers to an unknown activity ID: {', '.join(bad_refs)}.")

    # Bangun adjacency / in-degree untuk algoritma Kahn
    successors = {a: [] for a in activities}
    indegree = {a: 0 for a in activities}
    for _, row in df.iterrows():
        a = row['Activity']
        for dep in row['Dependencies']:
            successors[dep].append(a)
            indegree[a] += 1

    # Kahn's topological sort
    queue = [n for n in activities if indegree[n] == 0]
    topo = []
    while queue:
        n = queue.pop(0)
        topo.append(n)
        for succ in successors[n]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                queue.append(succ)

    if len(topo) != len(activities):
        raise gr.Error("A cycle has been detected in the dependencies (circular dependencies). Please fix the task dependencies.")

    # Siapkan map untuk pencarian cepat
    row_by_activity = {row['Activity']: row for _, row in df.iterrows()}
    # datetimes
    start_datetime = datetime.combine(start_date, datetime.min.time())

    ES_map = {}
    EF_map = {}

    # Forward pass menggunakan urutan topo
    for a in topo:
        row = row_by_activity[a]
        duration = int(row['Duration (Days)'])
        deps = row['Dependencies']

        if not deps:
            es = start_datetime
        else:
            # successor ES = max(EF dari predecessors) + 1 hari (karena EF inklusif)
            max_ef = max(EF_map[dep] for dep in deps)
            es = max_ef + pd.Timedelta(days=1)

        ef = es + pd.Timedelta(days=duration - 1) if duration > 0 else es  # konvensi inklusif
        ES_map[a] = es
        EF_map[a] = ef

    # Project finish
    project_finish = max(EF_map.values())

    # Backward pass menggunakan reverse topo
    LF_map = {}
    LS_map = {}

    for a in reversed(topo):
        row = row_by_activity[a]
        duration = int(row['Duration (Days)'])
        succs = successors[a]
        if not succs:
            lf = project_finish
        else:
            # LF = min(LS dari successors) - 1 hari
            min_succ_ls = min(LS_map[s] for s in succs)
            lf = min_succ_ls - pd.Timedelta(days=1)
        ls = lf - pd.Timedelta(days=duration - 1) if duration > 0 else lf
        LF_map[a] = lf
        LS_map[a] = ls

    # Bangun DataFrame hasil (dengan datetimes)
    df_result = df.copy()
    df_result['ES'] = df_result['Activity'].map(ES_map)
    df_result['EF'] = df_result['Activity'].map(EF_map)
    df_result['LS'] = df_result['Activity'].map(LS_map)
    df_result['LF'] = df_result['Activity'].map(LF_map)

    # Slack dan status
    df_result['Slack (Days)'] = (df_result['LF'] - df_result['EF']).dt.days
    df_result['Status'] = df_result['Slack (Days)'].apply(lambda x: 'Critical' if x == 0 else 'Non-Critical')
    critical_path = df_result[df_result['Status'] == 'Critical']['Activity'].tolist()

    # Format tampilan (string tanggal)
    df_display = df_result.copy()
    for col in ['ES', 'EF', 'LS', 'LF']:
        df_display[col] = pd.to_datetime(df_display[col]).dt.strftime('%Y-%m-%d')
    df_display['Dependencies'] = df_display['Dependencies'].apply(lambda x: ', '.join(x))

    return df_display, critical_path, df_result

def create_pert_chart(df, critical_path):
    dot = graphviz.Digraph(format="png")
    dot.attr(rankdir="LR", bgcolor="white")

    for _, row in df.iterrows():
        label = f"""<
        <TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0" COLOR="black">
        <TR><TD COLSPAN="2"><B>{row['Activity']}</B></TD></TR>
        <TR><TD>ES</TD><TD>{row['ES']}</TD></TR>
        <TR><TD>EF</TD><TD>{row['EF']}</TD></TR>
        <TR><TD>LS</TD><TD>{row['LS']}</TD></TR>
        <TR><TD>LF</TD><TD>{row['LF']}</TD></TR>
        <TR><TD>Dur.</TD><TD>{int(row['Duration (Days)'])}</TD></TR>
        <TR><TD>Slack</TD><TD>{int(row['Slack (Days)'])}</TD></TR>
        </TABLE>>"""
        dot.node(row["Activity"], label=label, shape="plaintext")

    for _, row in df.iterrows():
        deps_list = [d.strip() for d in row["Dependencies"].split(',') if d.strip()]
        for dep in deps_list:
            if dep:
                is_critical_edge = (dep in critical_path) and (row["Activity"] in critical_path)
                edge_color = "red" if is_critical_edge else "blue"
                edge_style = "bold"
                
                dot.edge(dep, row["Activity"], color=edge_color, style=edge_style)

    return dot

def create_gantt_chart(df, critical_path):
    df['ES'] = pd.to_datetime(df['ES']).dt.normalize()
    df['EF'] = pd.to_datetime(df['EF']).dt.normalize()
    df['Status'] = df['Activity'].apply(lambda x: 'Critical' if x in critical_path else 'Non-Critical')

    fig = px.timeline(
        df,
        x_start="ES",
        x_end="EF",
        y="Activity",
        color="Status",
        title="Project Gantt Chart",
        color_discrete_map={
            'Critical': 'rgb(230, 0, 0)',
            'Non-Critical': 'rgb(0, 110, 255)'
        },
        hover_data=["Duration (Days)", "ES", "EF", "LS", "LF", "Slack (Days)"]
    )

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        xaxis_title="Timeline",
        yaxis_title="Activity",
        legend_title="Task Type",
        hovermode="x unified"
    )
    return fig

# ----------------------------------------------------------------------------
# FUNGSI EVENT HANDLER GRADIO
# ----------------------------------------------------------------------------

def run_analysis(tasks_df, start_date_str):
    """
    Fungsi utama yang dipanggil oleh tombol "Run".
    Menerima input dari UI, memproses, dan mengembalikan output ke UI.
    """
    if tasks_df is None or tasks_df.empty or tasks_df['Activity'].isnull().all():
        raise gr.Error("No task data found. Please enter some tasks first.")
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        df = tasks_df.copy().dropna(subset=['Activity'])
        df['Dependencies'] = df['Dependencies'].fillna('')
    except Exception as e:
        raise gr.Error(f"Invalid input: {e}")

    df_display, critical_path, gantt_df = calculate_cpm(df, start_date)
    gantt_fig = create_gantt_chart(gantt_df, critical_path)
    dot = create_pert_chart(df_display, critical_path)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_png:
        f_png.write(dot.pipe(format='png'))
        pert_chart_path = f_png.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f_csv:
        df_display.to_csv(f_csv.name, index=False, encoding='utf-8')
        csv_download_path = f_csv.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as f_html:
        gantt_fig.write_html(f_html.name)
        gantt_download_path = f_html.name

    return (
        df_display,           # Output ke cpm_table
        pert_chart_path,      # Output ke pert_chart_img
        gantt_fig,            # Output ke gantt_chart_plot
        gr.File(csv_download_path, label="Download CPM Results (CSV)"),
        gr.File(pert_chart_path, label="Download PERT Chart (PNG)"),
        gr.File(gantt_download_path, label="Download Gantt Chart (HTML)")
    )

def load_from_csv(file_obj):
    """
    Dipanggil saat file CSV di-upload.
    """
    if file_obj is None:
        raise gr.Error("File not found.")
    try:
        imported_df = pd.read_csv(file_obj.name)
        required_cols = ['Activity', 'Duration (Days)', 'Dependencies']

        if not all(col in imported_df.columns for col in required_cols):
            raise gr.Error(f"Invalid CSV. Required columns are missing: {set(required_cols) - set(imported_df.columns)}")
        
        imported_df = imported_df[required_cols]
        imported_df['Dependencies'] = imported_df['Dependencies'].fillna('')
        
        gr.Info("Successfully imported CSV.")
        return imported_df
    
    except Exception as e:
        raise gr.Error(f"Error reading file: {e}")

def clear_data():
    """
    Dipanggil oleh tombol "Clear". Mengosongkan semua input dan output.
    """
    empty_df = pd.DataFrame(columns=["Activity", "Duration (Days)", "Dependencies"])
    return (
        empty_df,  # tasks_editor
        None,      # cpm_table
        None,      # pert_chart_img
        None,      # gantt_chart_plot
        None,      # csv_download
        None,      # pert_download
        None       # gantt_download
    )

# ----------------------------------------------------------------------------
# GRADIO INTERFACE
# ----------------------------------------------------------------------------
sample_data = {
    'Activity': ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
    'Duration (Days)': [5, 7, 6, 4, 6, 5, 4],
    'Dependencies': ['', 'A', 'A', 'B', 'C', 'D, E', 'F']
}
sample_df = pd.DataFrame(sample_data)

with gr.Blocks(title="Project Scheduler", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 📅 Project Scheduler")
    
    with gr.Row():
        with gr.Column(scale=3):
            gr.Markdown("### Use the table below to add, edit, or delete tasks. Add new rows at the bottom.")
            tasks_editor = gr.Dataframe(
                value=sample_df,
                headers=["Activity", "Duration (Days)", "Dependencies"],
                datatype=["str", "number", "str"],
                row_count=(len(sample_df), "dynamic"), # Memungkinkan penambahan baris
                col_count=(3, "fixed"),
                interactive=True,
                label="Project Tasks"
            )
        
        with gr.Column(scale=1):
            start_date_input = gr.Textbox(
                label="Project Start Date (Year-Month-Date)",
                value=datetime.today().strftime('%Y-%m-%d')
            )
            
            gr.Markdown("---")
            
            upload_button = gr.File(
                label="Import from CSV",
                file_types=[".csv"]
            )
            
            clear_button = gr.Button("🗑️ Clear All Data")

    gr.Markdown("---")
    
    run_button = gr.Button("Run Schedule Analysis", variant="primary", scale=1)
    
    gr.Markdown("---")
    
    gr.Markdown("## Project Analysis Results")
    
    with gr.Tabs():
        with gr.Tab("📊 CPM Results"):
            cpm_table = gr.Dataframe(label="CPM Results", interactive=False)
            csv_download = gr.File(label="Download CPM Results (CSV)")
        
        with gr.Tab("🚩 PERT Chart"):
            pert_chart_img = gr.Image(label="PERT Chart", type="filepath", interactive=False)
            pert_download = gr.File(label="Download PERT Chart (PNG)")
            
        with gr.Tab("🗓️ Gantt Chart"):
            gantt_chart_plot = gr.Plot(label="Gantt Chart")
            gantt_download = gr.File(label="Download Gantt Chart (HTML)")

    # ------------------------------------------------------------------------
    # KONEKSI EVENT LISTENER (LOGIKA UI)
    # ------------------------------------------------------------------------
    all_outputs = [cpm_table, pert_chart_img, gantt_chart_plot, csv_download, pert_download, gantt_download]
    
    run_button.click(
        fn=run_analysis,
        inputs=[tasks_editor, start_date_input],
        outputs=all_outputs
    )
    
    upload_button.upload(
        fn=load_from_csv,
        inputs=[upload_button],
        outputs=[tasks_editor]
    )
    
    clear_button.click(
        fn=clear_data,
        inputs=[],
        outputs=[tasks_editor] + all_outputs
    )

if __name__ == "__main__":
    demo.launch()