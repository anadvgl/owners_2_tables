import os
import pymysql
from sqlalchemy import create_engine, event, types
import pandas as pd
import json
from pandas import json_normalize
import getpass
from typing import Optional
import requests
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")
from loguru import logger
import httpx

# pmysqlsql credentials
mysqluser = os.getenv('mysqluser')
mysqlpwd = os.getenv('mysqlpwd')

# connect to mysql
mysql_conn_str = f"mysql+pymysql://{mysqluser}:{mysqlpwd}@host:port/db"
mysql_engine = create_engine(mysql_conn_str)
mysql_engine.execute("SET FOREIGN_KEY_CHECKS=0")
mysql_engine.connect()
print("Connected to mysql at: " + datetime.now().strftime('%H:%M:%S'))

query = ''' select SUBSTRING_INDEX(se.fullyQualifiedName, ".", 1) as system_name,
    SUBSTRING_INDEX(schema_name, ".", -2) as bd_name,
    te.team_name,
    SUBSTRING_INDEX(se.fullyQualifiedName, ".",-1) as table_name,
    SUBSTRING_INDEX(se.fullyQualifiedName, ".", 3) as schema_name,
    se.json as json,
    se.deleted as deleted
from openmetadata_db.table_entity se
join
    (select team.name team_name, team.id team_id, dse.fullyQualifiedName schema_name,dse.id schema_id
    from openmetadata_db.team_entity team, openmetadata_db.entity_relationship er, openmetadata_db.database_schema_entity dse
    where 1=1
    and er.fromId = team.id
    and er.toId = dse.id) te
on te.schema_name = SUBSTRING_INDEX(se.fullyQualifiedName, ".", 3)
    and se.id not in (SELECT toId
    FROM openmetadata_db.entity_relationship er
    where fromEntity = 'team'
    and toEntity = 'table') where deleted = 0 '''

df = pd.read_sql_query(query, mysql_engine)
print("df created at: " + datetime.now().strftime('%H:%M:%S'))

# close mysql connection
mysql_engine.dispose()

# omd credentials
userpwd = os.getenv("client_secret")

# connect to omd
def get_token(userpwd) -> str:
    data = {
        "grant_type": "client_credentials",
        "client_id": "open-metadata",
        "client_secret": userpwd,
    }
    response = httpx.post(
        "https://keycloak/token",
        data=data,
        verify=False,
        timeout=httpx.Timeout(300),
    )
    logger.info(f"Response status code: {response.status_code}")
    return response.json()["access_token"]

if len(df) == 0:
    print("Нет таблиц без владельцев")
else:
    print("Таблиц без владельцев: ", len(df))   
    df.loc[:, "json"] = df.loc[:, "json"].apply(json.loads)
    df['tableFQN'] = df['system_name'] + '.' + df['bd_name'] + '.' + df['table_name']

    df_new = pd.json_normalize(df['json'])
    df_new = df_new[['id', 'name', 'fullyQualifiedName', 'databaseSchema.fullyQualifiedName']]
    
    df_merged = pd.merge(df, df_new, left_on='tableFQN', right_on='fullyQualifiedName', how='left')
    df_merged = df_merged[['id', 'team_name']]
    df_merged.columns = ['table_id','team_name']
    df_final = df_merged.drop_duplicates().reset_index(drop=True)

    df_teams = df_final['team_name'].to_frame().drop_duplicates().reset_index(drop=True)
    
    teams_id = []
    for name in df_teams['team_name']:
        token = get_token(userpwd)
        response = requests.get(
            f"https://host/api/v1/teams/name/{name}",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            data={},
            verify=False,
        )
        result_id = response.json()
        teams_id.append(result_id)
        result_normalized = json_normalize(teams_id)
        result_normalized = result_normalized[["id"]]
        df_teams["team_id"] = result_normalized

    team_ids = dict(zip(df_teams.team_name,df_teams.team_id))
    df_final["team_id"] = df_final["team_name"].map(team_ids)

    i = 0
    for id, team_id in zip(df_final.table_id, df_final.team_id):
        token = get_token(userpwd)
        response = requests.patch(
                f"https://host/api/v1/tables/{id}",
                headers={"Content-Type": "application/json-patch+json", "Authorization": f"Bearer {token}"},
                data="""[{"op":"add", "path":"/owner",""" + " """""value": {"id":""" + f'"{team_id}' + """", "type": "team"}}]""",
                verify=False,
        )        
        if response.ok:
            result_id = response.json()
            res = response.json()
            res_name = res.get("name")
            print(f"Owner таблицы {res_name} добавлен")
            i += 1
        else:
            print(f"{response.status_code}, {response.text}")

    print("")    
    print("Обработанных таблиц: ", i)
