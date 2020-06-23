import json
import logging
from pathlib import Path
from .crawler import Crawler


LOGGER = logging.getLogger('citehub')
STORAGE_ROOT = Path('data')


def ensure_storage_root_exists():
    STORAGE_ROOT.mkdir(exist_ok=True, parents=True)


async def fetch_scopus(session, key, first_name, last_name):
    from .scopus import Scopus

    cached = STORAGE_ROOT / f'scopus {first_name} {last_name}'
    if cached.is_file():
        LOGGER.info('[scopus] Loading cached publications %s', first_name)
        with cached.open(encoding='utf-8') as fd:
            return json.load(fd)

    scopus = Scopus(session, key)

    # TODO This used to work from home but now it no longer does…
    #      Cannot test it unless we are at university.
    query = f'AUTHFIRST({first_name}) AND AUTHLASTNAME({last_name})'
    async for author in scopus.search_author(query):
        eid = author['eid']
        publications = await scopus.search_scopus(f'AU-ID({eid})')
        LOGGER.info('[scopus] Saving publications to cache %s', first_name)
        with cached.open('w', encoding='utf-8') as fd:
            json.dump(publications, fd)
        return publications

    # TODO consider using ORCID, Researched ID, Publons or Crossref
    #      if Scopus does not provide information about the publications.


async def fetch_aminer(session, auth, name_query):
    from .aminer import ArnetMiner

    cached = STORAGE_ROOT / f'aminer {name_query}'
    if cached.is_file():
        LOGGER.info('[aminer] Loading cached publications %s', name_query)
        with cached.open(encoding='utf-8') as fd:
            return json.load(fd)

    aminer = ArnetMiner(session, auth)
    author = await aminer.search_person(name_query)
    author_id = author['items'][0]['id']
    publications = await aminer.search_publications(author_id)
    LOGGER.info('[aminer] Saving publications to cache %s', name_query)
    with cached.open('w', encoding='utf-8') as fd:
        json.dump(publications, fd)
    return publications
