"""Shared SQLite storage. A separate file keeps the existing Tender database untouched."""
import os
import sqlite3
from pathlib import Path

DB = Path(os.getenv("ROAD_DB", Path(__file__).resolve().parent / "road.db"))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS traffic(scp TEXT PRIMARY KEY,name TEXT,lat REAL,lon REAL,count15 INTEGER,count60 INTEGER,speed15 REAL,observed TEXT);
    CREATE TABLE IF NOT EXISTS weather(scp TEXT PRIMARY KEY,name TEXT,lat REAL,lon REAL,air REAL,surface REAL,humidity REAL,wind REAL,observed TEXT);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT,title TEXT,detail TEXT,lat REAL,lon REAL,valid_from TEXT,valid_until TEXT,source_url TEXT);
    CREATE TABLE IF NOT EXISTS risk(id TEXT PRIMARY KEY,layer INTEGER,road TEXT,risk_level TEXT,condition TEXT,reference TEXT,district TEXT,minlat REAL,maxlat REAL,minlon REAL,maxlon REAL,geometry TEXT);
    CREATE TABLE IF NOT EXISTS crashes(id TEXT PRIMARY KEY,date TEXT,lat REAL,lon REAL,description TEXT,killed INTEGER,injured INTEGER);
    CREATE TABLE IF NOT EXISTS sources(key TEXT PRIMARY KEY,label TEXT,url TEXT,updated TEXT,count INTEGER,error TEXT);
    CREATE INDEX IF NOT EXISTS risk_bbox ON risk(minlat,maxlat,minlon,maxlon);
    CREATE INDEX IF NOT EXISTS crashes_bbox ON crashes(lat,lon,date);
    """)
    return con
