import plotly.graph_objects as go
from datetime import datetime
import plotly.express as px
import streamlit as st
import pandas as pd
import graphviz
import tempfile
import os

st.set_page_config(
    page_title="Project Scheduler",
    page_icon="📅",
    layout="wide"
)

st.title("Project Scheduler")

# Session state for task persistence
if 'tasks_df' not in st.session_state:
    sample_data = {
        'Activity': ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
        'Duration (Days)': [5, 7, 6, 4, 6, 5, 4],
        'Dependencies': ['', 'A', 'A', 'B', 'C', 'D, E', 'F']
    }
    st.session_state.tasks_df = pd.DataFrame(sample_data)

def calculate_cpm(df, start_date):
    """Compute CPM using explicit topological sort (Kahn). Returns:
        (df_display, critical_path, df_with_datetimes)
    """
    # Ensure inputs
    df = df.copy()
    df['Duration (Days)'] = pd.to_numeric(df['Duration (Days)'].fillna(1), errors='coerce').astype(int)
    df['Dependencies'] = df['Dependencies'].fillna('')

    # Normalize Activity names (strip) and Dependencies into lists (strip each)
    df['Activity'] = df['Activity'].astype(str).str.strip()
    def parse_deps(x):
        if pd.isna(x) or str(x).strip() == '':
            return []
        # split by comma and strip each token, ignore empties
        parts = [p.strip() for p in str(x).split(',')]
        return [p for p in parts if p != '']
    df['Dependencies'] = df['Dependencies'].apply(parse_deps)

    # Basic validation: unique activity names
    if df['Activity'].duplicated().any():
        dupes = df.loc[df['Activity'].duplicated(keep=False), 'Activity'].unique().tolist()
        st.error(f"Duplicate Activity IDs found: {', '.join(map(str, dupes))}. Activity names must be unique.")
        return None, None, None

    activities = df['Activity'].tolist()
    # Validate dependencies refer to existing activities
    all_deps = set(sum(df['Dependencies'].tolist(), []))
    bad_refs = [d for d in all_deps if d not in activities]
    if bad_refs:
        st.error(f"Dependencies reference unknown activity IDs: {', '.join(bad_refs)}.")
        return None, None, None

    # Build adjacency / in-degree for Kahn's algorithm
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
        st.error("Cycle detected in dependencies (circular dependency). Please fix task dependencies.")
        return None, None, None

    # Prepare maps for quick lookup
    row_by_activity = {row['Activity']: row for _, row in df.iterrows()}
    # datetimes
    start_datetime = datetime.combine(start_date, datetime.min.time())

    ES_map = {}
    EF_map = {}

    # Forward pass using topo order
    for a in topo:
        row = row_by_activity[a]
        duration = int(row['Duration (Days)'])
        deps = row['Dependencies']

        if not deps:
            es = start_datetime
        else:
            # successor ES = max(EF of predecessors) + 1 day (because inclusive EF)
            max_ef = max(EF_map[dep] for dep in deps)
            es = max_ef + pd.Timedelta(days=1)

        ef = es + pd.Timedelta(days=duration - 1) if duration > 0 else es  # inclusive convention
        ES_map[a] = es
        EF_map[a] = ef

    # Project finish
    project_finish = max(EF_map.values())

    # Backward pass using reverse topo
    LF_map = {}
    LS_map = {}

    for a in reversed(topo):
        row = row_by_activity[a]
        duration = int(row['Duration (Days)'])
        succs = successors[a]
        if not succs:
            lf = project_finish
        else:
            # LF = min(LS of successors) - 1 day
            min_succ_ls = min(LS_map[s] for s in succs)
            lf = min_succ_ls - pd.Timedelta(days=1)
        ls = lf - pd.Timedelta(days=duration - 1) if duration > 0 else lf
        LF_map[a] = lf
        LS_map[a] = ls

    # Build result DataFrame (with datetimes)
    df_result = df.copy()
    df_result['ES'] = df_result['Activity'].map(ES_map)
    df_result['EF'] = df_result['Activity'].map(EF_map)
    df_result['LS'] = df_result['Activity'].map(LS_map)
    df_result['LF'] = df_result['Activity'].map(LF_map)

    # Slack and status
    df_result['Slack (Days)'] = (df_result['LF'] - df_result['EF']).dt.days
    df_result['Status'] = df_result['Slack (Days)'].apply(lambda x: 'Critical' if x == 0 else 'Non-Critical')
    critical_path = df_result[df_result['Status'] == 'Critical']['Activity'].tolist()

    # Display formatting (string dates)
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
        for dep in row["Dependencies"]:
            dep = dep.strip(', ').strip()
            if dep:
                is_critical_edge = (dep in critical_path) and (row["Activity"] in critical_path)
                edge_color = "red" if is_critical_edge else "blue"
                edge_style = "bold"
                
                dot.edge(dep, row["Activity"], color=edge_color, style=edge_style)

    return dot

def create_gantt_chart(df, critical_path):
    df['ES'] = pd.to_datetime(df['ES'])
    df['EF'] = pd.to_datetime(df['EF'])
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


col_editor, col_controls = st.columns([3, 1])

with col_editor:
    st.info("Use the table below to add, edit, or delete tasks. Add new rows at the bottom.")
    
    edited_df = st.data_editor(
        st.session_state.tasks_df,
        num_rows="dynamic",
        width="stretch",
        key="data_editor_main",
        column_config={
            "Activity": st.column_config.TextColumn(required=True),
            "Duration (Days)": st.column_config.NumberColumn(min_value=1, step=1, required=True),
            "Dependencies": st.column_config.TextColumn(help="Separate multiple dependencies with commas (e.g., A, B)")
        }
    )

    st.session_state.tasks_df = edited_df.copy()

with col_controls:
    start_date = st.date_input("Project Start Date", value=datetime.today())
    st.markdown("---")

    uploader_key = "file_uploader" if not st.session_state.get("reset_uploader") else "file_uploader_reset"
    uploaded_file = st.file_uploader(
        "Import from CSV",
        type="csv",
        key=uploader_key
    )

    if uploaded_file is not None and not st.session_state.get("csv_imported", False):
        try:
            imported_df = pd.read_csv(uploaded_file)
            required_cols = ['Activity', 'Duration (Days)', 'Dependencies']

            if not all(col in imported_df.columns for col in required_cols):
                st.error(f"Invalid CSV. Missing required columns: {set(required_cols) - set(imported_df.columns)}")
            else:
                imported_df = imported_df[required_cols]
                st.session_state.tasks_df = imported_df.copy()
                st.success("CSV imported successfully.")

                st.session_state["csv_imported"] = True
                st.session_state["reset_uploader"] = False

                for key in ['processed_df', 'critical_path', 'gantt_fig']:
                    if key in st.session_state:
                        del st.session_state[key]

                st.rerun()

        except Exception as e:
            st.error(f"File read error: {e}")
        
    if st.button("🗑️ Clear All Data", type="secondary", width="stretch"):
        st.session_state.tasks_df = pd.DataFrame(columns=["Activity", "Duration (Days)", "Dependencies"])
        for key in ['processed_df', 'critical_path', 'gantt_fig', 'csv_imported']:
            if key in st.session_state:
                del st.session_state[key]
        
        st.session_state["reset_uploader"] = True

        st.rerun()

st.markdown("---")

if st.button("Run Schedule Analysis", type="primary", width="stretch", help="Click to calculate the Critical Path Method (CPM) and generate the PERT and Gantt Chart"):
    if st.session_state.tasks_df.empty or st.session_state.tasks_df['Activity'].isnull().all():
        st.warning("No task data found. Please enter some tasks first.")
    else:
        df = st.session_state.tasks_df.copy()
        df = df.dropna(subset=['Activity'])

        with st.spinner("Calculating project schedule..."):
            processed_df, critical_path, gantt_df = calculate_cpm(df, start_date)

            if processed_df is not None:
                st.session_state.processed_df = processed_df
                st.session_state.critical_path = critical_path
                st.session_state.gantt_fig = create_gantt_chart(gantt_df, critical_path)
                st.success("Schedule analysis completed!")
            else:
                for key in ['processed_df', 'critical_path', 'gantt_fig']:
                    if key in st.session_state:
                        del st.session_state[key]

if 'processed_df' in st.session_state:
    st.markdown("---")
    st.subheader("Project Analysis Results")

    tab1, tab2, tab3 = st.tabs(["📊 CPM Results", "🚩 PERT Chart", "🗓️ Gantt Chart"])

    with tab1:
        st.header("CPM Results")
        df_display = st.session_state.processed_df
        st.dataframe(df_display, width="stretch", hide_index=True)

        csv_data = df_display.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download CPM Results (CSV)",
            data=csv_data,
            file_name="project_cpm_results.csv",
            mime="text/csv",
        )

    with tab2:
        st.header("PERT Chart")
        if st.session_state.critical_path:
            try:
                dot = create_pert_chart(st.session_state.processed_df, st.session_state.critical_path)
                st.graphviz_chart(dot, width="content")
                
                png_data = dot.pipe(format='png')
                st.download_button(
                    label="📥 Download PERT Chart (PNG)",
                    data=png_data,
                    file_name="project_pert_chart.png",
                    mime="image/png",
                    width="content"
                )
            except Exception as e:
                st.error(f"Failed to create the chart: {e}")
        else:
            st.warning("No PERT Chart found.")

    with tab3:
        st.header("Gantt Chart")
        st.info("Hover over bars for details. Use the toolbar at the top-right to zoom, pan, or download as a PNG.")
        st.plotly_chart(st.session_state.gantt_fig, width="stretch")

        html_data = st.session_state.gantt_fig.to_html()
        st.download_button(
            label="📤 Download Gantt Chart (HTML)",
            data=html_data,
            file_name="project_gantt_chart.html",
            mime="text/html",
        )