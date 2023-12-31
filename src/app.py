
from src.utils import *

st.title("Species distribution model")

# -------- Load data --------

data_path = st.text_input("Enter path to data", "/data")
df_spc, df_cls, df_out, feature_names, reserves = load_data()


print("Rows with null values in df_out, dropping them")
print(df_out[df_out.isnull().any(axis=1)])

df_out = df_out.dropna()

min_obs = 10
df_spc = df_spc.groupby('species').filter(lambda x: len(x) >= min_obs)

feature_types = infer_feature_types(df_spc[feature_names])

features = {}

features['cont'] = [f for f in feature_names if feature_types[f] == 'Continuous']
features['cat'] = [f for f in feature_names if feature_types[f] == 'Categorical']

# -------- Streamlit code --------

# select model
available_models = ['CatBoost', 'Logistic Regression', 'MaxEnt']
selected_model = st.selectbox("Select model", available_models)

# select model class
model_class = None
if selected_model == 'CatBoost':
    model_class = ModelBirdCatBoost
elif selected_model == 'Logistic Regression':
    model_class = ModelBirdLogisticRegression
elif selected_model == 'MaxEnt':
    model_class = ModelBirdMaxEnt

available_years = df_spc['year'].unique()
selected_years = st.multiselect(
    'Select survery years',
    available_years)

if len(selected_years) == 0:
    st.stop()

df_spc = df_spc.query('year in @selected_years')

available_ranks = df_spc['conservation_status'].unique()
container_ranks = st.container()
all_ranks = st.checkbox("Select all conservation ranks")

if all_ranks:
    selected_ranks = container_ranks.multiselect("Select conservation rank:", 
        available_ranks, available_ranks)
else:
    selected_ranks = container_ranks.multiselect("Select conservation rank:",
        available_ranks)
    
if len(selected_ranks) == 0:
    st.stop()

# filter the available species by the selected years
available_species = (
    df_spc
    .query('year in @selected_years')
    .query('conservation_status in @selected_ranks')
)

available_species = list(available_species['species'].unique())
available_species.sort()

container_species = st.container()
all_species = st.checkbox("Select all species")
if all_species:
    selected_species = container_species.multiselect("Select species", 
        available_species, available_species)  
else:
    selected_species = container_species.multiselect("Select species",
        available_species)

if len(selected_species) == 0:
    st.stop()

st.subheader("Select continuous variables")

cont_vars_container = st.container()
all_cont_vars = st.checkbox("Select all continuous variables")
if all_cont_vars:
    selected_cont_vars = cont_vars_container.multiselect("Continuous variables", features['cont'], features['cont'])
else:
    selected_cont_vars = cont_vars_container.multiselect("Continuous variables", features['cont'])

variables_cont = selected_cont_vars

if len(variables_cont) == 0:
    st.stop()

st.write("Selected categorical variables:")

models_with_cats = ['CatBoost', 'MaxEnt']
if selected_model in models_with_cats:
    value = True
else:
    value = False

variables_cat = features['cat']

if len(variables_cat) == 0:
    st.stop()

cat_vars_container = st.container()
all_cat_vars = st.checkbox("Select all categorical variables")
if all_cat_vars:
    selected_cat_vars = cat_vars_container.multiselect("Categorical variables", features['cat'], features['cat'])
else:
    selected_cat_vars = cat_vars_container.multiselect("Categorical variables", features['cat'])

variables_cat = selected_cat_vars

if reserves is not None:
    plot_nature_reserves = st.checkbox("Plot nature reserves", value=False)
else:
    plot_nature_reserves = False

plot_feature_importance = st.checkbox("Plot feature importance", value=False)

if len(selected_species) > 1:
    agg_method = st.selectbox("Select aggregation method for multiple species",
                              ['mean', 'max', 'min', 'median'],
                              index=0)
else:
    agg_method = 'mean'

to_threshold = st.checkbox("To threshold probabilities", value=False)
if to_threshold:
    thresholds = {}
    for spc in selected_species:
        thresholds[spc] = st.slider(f"Threshold for {spc}", 0.0, 1.0, 0.05)

# button for running the model
run_model = st.button("Run model")

if not run_model:
    st.stop()

# -------- / Streamlit code --------

cfg = {
    'species': selected_species,
    'features': variables_cont + variables_cat,
    'features_cont': variables_cont,
    'features_cat': variables_cat,
    'survey_years': selected_years,
    'model_name': selected_model,
}

years = cfg['survey_years']

aggregation_type = "Treat separately"

to_ohe = len(variables_cat) > 0

if aggregation_type == "Group together":
    # currently not available
    model = model_class(to_scale=True, to_ohe=False, cfg=cfg)
    res = run_exp(model, df_cls, df_out, cfg=cfg)
    probas_list = [res['y_pred_out']]
    models_list = None
else:  # Treat separately
    probas_list = []
    models_list = []
    for species in selected_species:
        cfg_single_species = cfg.copy()
        cfg_single_species['species'] = [species]
        model_single_species = model_class(to_scale=True, to_ohe=to_ohe, cfg=cfg_single_species)
        res_single_species = run_exp(model_single_species, df_cls, df_out, cfg=cfg_single_species)
        probas_list.append(res_single_species['y_pred_out'])
        models_list.append(model_single_species)
    probas_list = np.array(probas_list)

if to_threshold:
    for i, spc in enumerate(selected_species):
        probas_list[i] = (probas_list[i] > thresholds[spc]).astype(int)

if agg_method == 'mean':
    probas = np.mean(probas_list, axis=0)
elif agg_method == 'max':
    probas = np.max(probas_list, axis=0)
elif agg_method == 'min':
    probas = np.min(probas_list, axis=0)
elif agg_method == 'median':
    probas = np.median(probas_list, axis=0)

df_res = df_out.copy()
df_res['pred_proba'] = probas
df_res['x'] = df_res['geometry'].apply(lambda x: x.centroid.x)
df_res['y'] = df_res['geometry'].apply(lambda x: x.centroid.y)

df_res = df_res[['x', 'y', 'geometry', 'pred_proba']]

years = cfg['survey_years']
df_spc = df_spc.query('year in @years')
df_spc_info = get_spc_info(df_spc, cfg['species'])

st.table(df_spc_info)

fig_map, ax_map = plot_probas_on_map(
    df_res=df_res,
    df_out=df_out,
    df_spc=df_spc.query('year in @years'),
    spc_list=cfg['species'],
    resolution=500,
    plot_other_species=True,
    plot_nature_reserves=plot_nature_reserves,
    reserves=reserves)

st.pyplot(fig_map)
if plot_feature_importance:
    if len(models_list) == 1:
        # for now only plot if one model, because shap values
        # can be expensive to compute
        for model in models_list:
            fig_fr = plot_feature_relevance(model, cfg['model_name'])
            st.pyplot(fig_fr)


df_res_to_save = df_res[['x', 'y', 'pred_proba']]
df_res_to_save = df_res_to_save.rename(columns={'x': 'longitude', 'y': 'latitude',
                                                'pred_proba': 'probability'})

res_csv = df_res_to_save.to_csv(index=False)
st.download_button(
    label="Download predictions CSV",
    data=res_csv,
    file_name="probas_model.csv",
    mime="text/csv",
)

fig_map.savefig("map.png")

with open("map.png", "rb") as file:
    btn = st.download_button(
            label="Download map",
            data=file,
            file_name="map.png",
            mime="image/png"
          )