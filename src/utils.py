import geopandas as gpd
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap
from shapely.geometry import Point
from shapely import wkt

from scipy.interpolate import griddata
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import log_loss
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score
from sklearn.metrics import roc_auc_score

import streamlit as st

from src.models import *


@st.cache_data
def load_data(path_data='data/'):
    try:
        df_geo = gpd.read_file('{}df_geo.geojson'.format(path_data))
    except:
        df_geo = pd.read_csv('{}df_geo.csv'.format(path_data))

        df_geo['geometry'] = df_geo['geometry'].apply(wkt.loads)
        df_geo = gpd.GeoDataFrame(df_geo, geometry='geometry')
        df_geo.to_file('{}df_geo.geojson'.format(path_data), driver='GeoJSON')

    df_survey = pd.read_csv('{}survey.csv'.format(path_data))
    cols_survey_point = ['date', 'latitude', 'longitude', 'survey_name', 'species', 'conservation_status', 'reserve_status']
    
    # feature names are all the columns except the ones in cols_survey_point
    feature_names = df_survey.columns.tolist()
    feature_names = [f for f in feature_names if f not in cols_survey_point]

    df_spc, df_survey = preproc(df_survey, feature_names, to_impute=True, df_geo=df_geo)
    try:
        reserves = gpd.read_file('{}reserves.shp'.format(path_data))
    except:
        reserves = None

    return df_spc, df_survey, df_geo, feature_names, reserves


def preproc(df_survey, feature_names, to_impute = True, df_geo=None):
    df_survey['date'] = pd.to_datetime(df_survey['date'])
    df_survey['month'] = df_survey['date'].dt.month
    df_survey['year'] = df_survey['date'].dt.year

    convs_map = pd.DataFrame({'status_name': ['LC', 'NT', 'VU', 'EN', 'CR'], 'status_rank': [1, 2, 3, 4, 5]})
    df_survey['conservation_rank'] = df_survey['conservation_status'].map(convs_map.set_index('status_name')['status_rank'])
     
    convs_map_2 = pd.DataFrame({'status_name': ['LC', 'NT', 'VU', 'EN', 'CR'], 
                                'status_full_name': ['Least Concerned', 'Near Threatened', 'Vulnerable', 'Endangered', 'Critically Endangered']})

    df_survey['conservation_status_full'] = df_survey['conservation_status'].map(convs_map_2.set_index('status_name')['status_full_name'])

    df_survey['year'] = df_survey['year'].astype('category')

    df_survey = df_survey.rename(columns={'latitude': 'y', 'longitude': 'x'})

    if "ndvi" in df_survey.columns:
        df_survey['ndvi'] = df_survey['ndvi'].astype(float)
        df_survey['ndvi'] = df_survey.groupby(['x', 'y'])['ndvi'].transform('mean')

    df_survey['species_observed'] = ~df_survey['species'].isna()

    df_survey = gpd.GeoDataFrame(df_survey, geometry=gpd.points_from_xy(df_survey.x, df_survey.y))

    if to_impute:
        df_survey = impute_using_nearest_neighbor(df_survey, df_geo, feature_names)

    df_survey = df_survey.copy()
    df_survey = df_survey.reset_index(drop=True)

    df_spc_obs_only = df_survey[~df_survey.species.isna()]
    df_spc_obs_only = df_spc_obs_only.reset_index(drop=True)

    return df_spc_obs_only, df_survey


def impute_using_nearest_neighbor(df_survey, df_all, vars_to_impute):
    df_all['centroid'] = df_all['geometry'].apply(lambda x: x.centroid)
    df_all['x'] = df_all['centroid'].apply(lambda x: x.x)
    df_all['y'] = df_all['centroid'].apply(lambda x: x.y)

    nn = NearestNeighbors(n_neighbors=1)
    nn.fit(df_all[['x', 'y']])

    for var in vars_to_impute:
        if df_survey[var].isnull().any():
            missing_rows = df_survey[var].isnull()
            nearest_indices = nn.kneighbors(df_survey.loc[missing_rows, ['x', 'y']], return_distance=False).flatten()
            df_survey.loc[missing_rows, var] = df_all.iloc[nearest_indices][var].values
    return df_survey


def make_single_bird_labels(df_survey, spc=None):
    df_survey = df_survey.copy()

    labels = np.where(df_survey['species'].isin(spc), 1, 0).reshape(-1, 1)
    df_survey['label'] = labels

    df_survey = (
        df_survey
        .sort_values(by=['x', 'y', 'year', 'label'], ascending=False)
        .drop_duplicates(subset=['x', 'y', 'year'], keep='first')
        .reset_index(drop=True)
        )
    
    labels = df_survey['label'].values.reshape(-1, 1)
    return df_survey, labels

def preproc_for_model(df_cls, df_out, cfg):

    df_cls = df_cls.copy()

    survey_years = cfg['survey_years']
    df_cls = df_cls.query('year in @survey_years').reset_index(drop=True)

    spc = cfg['species']

    assert set(spc).issubset(set(df_cls['species'].unique()))

    df_cls, y_train = make_single_bird_labels(df_cls, spc=spc)

    features = cfg['features']
    X_train = df_cls.loc[:, features]

    for col in X_train.columns:
        if X_train[col].dtype == 'object':
            X_train[col] = X_train[col].astype('category')

    if df_out is not None:
        df_out = df_out.copy().reset_index(drop=True)

        if 'year' in df_out.columns:
            df_out = df_out.query('year in @survey_years').reset_index(drop=True)

        df_out = df_out.loc[:, features]
        for col in df_out.columns:
            if df_out[col].dtype == 'object':
                df_out[col] = df_out[col].astype('category')
    
    return X_train, y_train, df_out


def run_exp(model,
            df_cls,
            df_out,
            cfg=None,
            out_preds=True):

    X_train, y_train, df_out = preproc_for_model(df_cls, df_out, cfg)

    model = model.fit(X_train, y_train)
    y_pred_train = model.predict_proba(X_train)

    res = dict()
    features = cfg['features']

    if out_preds:
        X_out = df_out[features]
        y_pred_out = model.predict_proba(X_out)
        res['y_pred_out'] = y_pred_out

    res['y_pred_train'] = y_pred_train
    res['model'] = model
    res['y_train'] = y_train
    return res


def plot_probas_on_map(df_res,
                       df_out,
                       df_spc,
                       spc_list=None,
                       figsize=(10, 10),
                       resolution=100,
                       plot_other_species=False,
                       plot_nature_reserves=False,
                       reserves=None,
                       ):

    df_spc = gpd.GeoDataFrame(df_spc, geometry=gpd.points_from_xy(df_spc.x, df_spc.y))

    # Calculate centroids and create interpolation grid
    df_res['centroid'] = df_res.geometry.centroid
    x = np.array([pt.x for pt in df_res.centroid])
    y = np.array([pt.y for pt in df_res.centroid])
    z = df_res['pred_proba'].values
    xi, yi = np.mgrid[min(x):max(x):resolution*1j, min(y):max(y):resolution*1j]

    # Perform IDW interpolation
    zi = griddata((x, y), z, (xi, yi), method='linear')
    
    # Plot interpolated data
    fig, ax = plt.subplots(figsize=figsize)
    cs = ax.contourf(xi, yi, zi, cmap='viridis', levels=10)
    fig.colorbar(cs, ax=ax)

    if spc_list:
        # only with the species
        df_birds_spc = df_spc.query('species in @spc_list')
        p = df_birds_spc.plot(ax=ax, marker='o', color='red', markersize=6,
                              label="chosen species")
        if plot_other_species:
            df_birds_other = df_spc.query('species not in @spc_list')
            df_birds_other = df_birds_other.query('x not in @df_birds_spc.x and y not in @df_birds_spc.y')
            df_birds_other.plot(ax=ax, marker='o', color='white', markersize=0.5,
                                alpha=0.5, label='other species')
        ax.legend()

    else:
        # plot all survey points
        df_spc.plot(ax=ax, marker='o', color='pink', markersize=3)

    if plot_nature_reserves and reserves is not None:

        colors = ['brown', 'hotpink', 'white', 'coral']  # replace with the actual colors
        cmap = ListedColormap(colors)

        status_to_color = {status: color for status, color in zip(reserves['STATUS_DES'].unique(), colors)}

        reserves['color'] = reserves['STATUS_DES'].map(status_to_color)
        reserves['color'] = reserves['color'].fillna('black')

        reserves.boundary.plot(ax=ax, edgecolor=reserves['color'], linewidth=2.0,
                               linestyle='--', alpha=0.6)

        reserves.plot(ax=ax,
                       edgecolor=reserves['color'],
                       column='STATUS_DES',
                       alpha=0.03,
                       color="white"
                       )

        color_mapping = dict(zip(reserves['STATUS_DES'].unique(), reserves['color'].unique()))
        patches = [mpatches.Patch(color=color, label=label) for label, color in color_mapping.items()]

        ax.legend(handles=patches, bbox_to_anchor=(-0.5, 1), loc='upper left', borderaxespad=0.)

        xmin, ymin, xmax, ymax = df_out.total_bounds
        ax.set_xlim([xmin, xmax])
        ax.set_ylim([ymin, ymax])

    title = "Probability for bird presence"
    ax.set_title(title)
    return fig, ax


def plot_dot_whisker(coefs_stats, figsize=(5,6), dpi=100):
    coefs = pd.DataFrame(coefs_stats.params, columns=['coef'])
    coefs['std'] = coefs_stats.bse
    coefs['pval'] = coefs_stats.pvalues
    coefs['abs_coef'] = coefs.coef.abs()
    coefs.sort_values(by='abs_coef', ascending=False)

    to_include = coefs[1:].sort_values(by='abs_coef') 
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    ax.scatter(to_include.coef, range(len(to_include)), color="#1a9988", zorder=2)
    ax.set_yticks(range(len(to_include)), to_include.index)
    ax.set_xlabel("Coefficient Value")

    for idx, ci in enumerate(coefs_stats.conf_int().loc[to_include.index].iterrows()):
        ax.hlines(idx, ci[1][0], ci[1][1], color="#eb5600", zorder=1, linewidth=3)

    p = plt.axline((0,0), (0,1), color="#eb5600", linestyle="--")
    title = "Coefficients and 95% Confidence Intervals"
    ax.set_title(title)

    return fig


def get_spc_info(df_birds, spc):
    df_spc = df_birds.query('species in @spc').reset_index(drop=True)

    dfs = (
        df_spc
        .groupby(['species', 'conservation_rank', 'conservation_status'])
        .size()
        .reset_index(name='number_observations')
        .sort_values(by='number_observations', ascending=False)
        .reset_index(drop=True)
    )

    # get total num of obs in years where species is not None
    # assuming was filtered by years already
    num_total_obs = df_birds.shape[0]

    dfs['percent_in_year'] = 100 * dfs['number_observations'] / num_total_obs
    dfs['percent_in_year'] = dfs['percent_in_year'].round(2)
    return dfs


def infer_feature_types(df, unique_threshold=0.1):
    known_cont_features = ['veg_cover']
    inferred_types = {}

    # veg_cover should be a continuous feature
    for column in df.columns:
        unique_count = len(df[column].unique())
        total_count = len(df[column])

        # If the feature is numeric
        if pd.api.types.is_numeric_dtype(df[column]):
            # And the ratio of unique values to total values is above the threshold
            if unique_count / total_count > unique_threshold or column in known_cont_features:
                inferred_types[column] = 'Continuous'
            else:
                inferred_types[column] = 'Categorical'
        # If feature is not numeric (e.g., object, bool, datetime64)
        else:
            inferred_types[column] = 'Categorical'

    return inferred_types


def plot_feature_relevance(model, model_name):
    """
    Plot the feature relevance of the model.
    """
    if model_name in ["CatBoost", "MaxEnt"]:
        return model.plot_feature_importances()
    elif model_name == "Logistic Regression":
        return plot_dot_whisker(model.model)

