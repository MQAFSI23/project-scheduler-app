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
    """Compute Critical Path Method (ES, EF, LS, LF, Slack) and generate data for the PERT and Gantt charts."""

    # Ensure numeric duration
    df['Duration (Days)'] = pd.to_numeric(df['Duration (Days)'], errors='coerce').fillna(0).astype(int)

    # Split dependencies into lists
    df['Dependencies'] = df['Dependencies'].apply(lambda x: x.split(', ') if pd.notna(x) and x.strip() != '' else [])

    # Initialize columns
    df['ES'] = pd.NaT
    df['EF'] = pd.NaT
    df['LS'] = pd.NaT
    df['LF'] = pd.NaT

    start_datetime = datetime.combine(start_date, datetime.min.time())
    ef_map = {}

    # Forward Pass (ES & EF)
    for _ in range(len(df)):
        for idx, row in df.iterrows():
            deps = row['Dependencies']

            if not deps:
                es = start_datetime
            else:
                max_dep_ef = pd.NaT
                all_done = True
                for dep in deps:
                    dep = dep.strip()
                    if dep not in ef_map:
                        all_done = False
                        break
                    if pd.isna(max_dep_ef) or ef_map[dep] > max_dep_ef:
                        max_dep_ef = ef_map[dep]
                es = (max_dep_ef + pd.Timedelta(days=1)) if all_done else pd.NaT

            if pd.notna(es):
                ef = es + pd.Timedelta(days=int(row['Duration (Days)']) - 1) # inclusive
                df.at[idx, 'ES'] = es
                df.at[idx, 'EF'] = ef
                ef_map[row['Activity']] = ef

    if df['ES'].isna().any():
        st.error("Calculation failed. Please check for circular or invalid dependencies.")
        return None, None, None

    # Backward Pass (LS & LF)
    project_finish = df['EF'].max()
    df['LF'] = project_finish

    successors_map = {task: [] for task in df['Activity']}
    for _, row in df.iterrows():
        for dep in row['Dependencies']:
            dep = dep.strip()
            if dep in successors_map:
                successors_map[dep].append(row['Activity'])

    for idx in reversed(df.index):
        activity = df.at[idx, 'Activity']
        successors = successors_map[activity]
        if not successors:
            lf = project_finish
        else:
            min_succ_ls = pd.NaT
            for succ in successors:
                succ_ls = df.loc[df['Activity'] == succ, 'LS'].values[0]
                if pd.isna(min_succ_ls) or succ_ls < min_succ_ls:
                    min_succ_ls = succ_ls

            # LF (predecessor) = LS (successor) - 1 day
            # reflection of: ES (successor) = EF (predecessor) + 1 day
            lf = min_succ_ls - pd.Timedelta(days=1) if pd.notna(min_succ_ls) else project_finish

        df.at[idx, 'LF'] = lf

        # LS = LF - Duration - 1 day
        # reflection of: EF = ES + Duration - 1 day
        df.at[idx, 'LS'] = lf - pd.Timedelta(days=int(df.at[idx, 'Duration (Days)']) - 1)

    # Slack and Critical Path
    df['Slack (Days)'] = (df['LF'] - df['EF']).dt.days
    df['Status'] = df['Slack (Days)'].apply(lambda x: 'Critical' if x == 0 else 'Non-Critical')
    critical_path = df[df['Status'] == 'Critical']['Activity'].tolist()

    # Display formatting
    df_display = df.copy()
    for col in ['ES', 'EF', 'LS', 'LF']:
        df_display[col] = df_display[col].dt.strftime('%Y-%m-%d')
    df_display['Dependencies'] = df_display['Dependencies'].apply(lambda x: ', '.join(x))

    return df_display, critical_path, df

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
    table_key = f"data_editor_{hash(str(st.session_state.tasks_df.to_json()))}"
    
    edited_df = st.data_editor(
        st.session_state.tasks_df,
        num_rows="dynamic",
        width="stretch",
        key=table_key,
        column_config={
            "Activity": st.column_config.TextColumn(required=True),
            "Duration (Days)": st.column_config.NumberColumn(min_value=0, step=1, required=True),
            "Dependencies": st.column_config.TextColumn(help="Separate multiple dependencies with commas (e.g., A, B)")
        }
    )

    if not edited_df.equals(st.session_state.tasks_df):
        st.session_state.tasks_df = edited_df.copy()

with col_controls:
    start_date = st.date_input("Project Start Date", value=datetime.today())
    st.markdown("---")

    # if 'temp_file_path' not in st.session_state:
    #     st.session_state.temp_file_path = None

    uploaded_file = st.file_uploader(
        "Import from CSV",
        type="csv",
        key="file_uploader" if not st.session_state.get("reset_uploader") else "file_uploader_reset"
    )

    if uploaded_file is not None:
        try:
            # Clean up old temporary file if it exists
            # if st.session_state.temp_file_path and os.path.exists(st.session_state.temp_file_path):
            #     os.remove(st.session_state.temp_file_path)

            # with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp_file:
            #     tmp_file.write(uploaded_file.getbuffer())
            #     st.session_state.temp_file_path = tmp_file.name

            # imported_df = pd.read_csv(st.session_state.temp_file_path)
            imported_df = pd.read_csv(uploaded_file)
            required_cols = ['Activity', 'Duration (Days)', 'Dependencies']

            if list(imported_df.columns) != required_cols:
                st.error(
                    f"Invalid CSV format. The file must have exactly these 3 columns in order: "
                    f"{', '.join(required_cols)}.\n\n"
                    f"Uploaded columns: {', '.join(imported_df.columns)}"
                )
            else:
                st.session_state.tasks_df = imported_df.copy()
                st.success(f"CSV imported successfully.")

                st.session_state["reset_uploader"] = False

        except Exception as e:
            st.error(f"File read error: {e}")
        
    if st.button("🗑️ Clear All Data", type="secondary", width="stretch"):
        # if st.session_state.temp_file_path and os.path.exists(st.session_state.temp_file_path):
        #     os.remove(st.session_state.temp_file_path)
        #     st.session_state.temp_file_path = None

        st.session_state.tasks_df = pd.DataFrame(columns=["Activity", "Duration (Days)", "Dependencies"])
        for key in ['processed_df', 'critical_path', 'gantt_fig']:
            if key in st.session_state:
                del st.session_state[key]
        
        st.session_state["reset_uploader"] = True
        st.rerun()

st.markdown("---")

if st.button("Run Schedule Analysis", type="primary", width="stretch", help="Click to calculate the Critical Path Method (CPM) and generate the PERT and Gantt Chart"):
    # if not edited_df.equals(st.session_state.tasks_df):
    #     st.session_state.tasks_df = edited_df.copy()

    if st.session_state.tasks_df.empty or st.session_state.tasks_df['Activity'].isnull().all():
        st.warning("No task data found. Please enter some tasks first.")
    else:
        df = st.session_state.tasks_df.copy()
        # st.info(f"Using data from: {'uploaded file' if st.session_state.temp_file_path else 'manual input'}")

        df = df.dropna(subset=['Activity'])
        df = df[df['Activity'].str.strip() != '']
        df['Duration (Days)'] = pd.to_numeric(df['Duration (Days)'], errors='coerce').fillna(0)
        df['Dependencies'] = df['Dependencies'].fillna('')

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