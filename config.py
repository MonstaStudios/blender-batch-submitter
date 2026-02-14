"""
Centralized configuration loader.
Loads environment variables from .env and exposes simple SETTINGS and convenience variables.
Also provides CGRU initialization helper to populate cgruconfig.VARS and adjust sys.path when `CGRU_LOCATION` is set.
"""
from dotenv import load_dotenv
import os
import sys
import logging

load_dotenv()

# Basic settings pulled from environment
SETTINGS = {
    'KITSU_API_URL': os.getenv('KITSU_API_URL', ''),
    'KITSU_USER': os.getenv('KITSU_USER', ''),
    'KITSU_PASSWORD': os.getenv('KITSU_PASSWORD', ''),
    'KITSU_DEFAULT_PROD': os.getenv('KITSU_DEFAULT_PROD', ''),
    'AFANASY_SERVER': os.getenv('AFANASY_SERVER', ''),
    'AFANASY_PORT': int(os.getenv('AFANASY_PORT', 0)) if os.getenv('AFANASY_PORT') else None,
    'CGRU_LOCATION': os.getenv('CGRU_LOCATION', ''),
    'BLENDER_PATH': os.getenv('BLENDER_PATH', ''),
    'ENVIRONMENT': os.getenv('ENVIRONMENT', 'development'),
    'LOG_LEVEL': os.getenv('LOG_LEVEL', 'INFO')
}

# Backwards-compatible module-level names
KITSU_API_URL = SETTINGS['KITSU_API_URL']
KITSU_USER = SETTINGS['KITSU_USER']
KITSU_PASSWORD = SETTINGS['KITSU_PASSWORD']
KITSU_DEFAULT_PROD = SETTINGS['KITSU_DEFAULT_PROD']
AFANASY_SERVER = SETTINGS['AFANASY_SERVER']
AFANASY_PORT = SETTINGS['AFANASY_PORT']
CGRU_LOCATION = SETTINGS['CGRU_LOCATION']
BLENDER_PATH = SETTINGS['BLENDER_PATH']
ENVIRONMENT = SETTINGS['ENVIRONMENT']
LOG_LEVEL = SETTINGS['LOG_LEVEL']

# Configure logging for modules that import config
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger('repo.config')


def init_cgru():
    """Initialize CGRU/Afanasy paths and cgruconfig.VARS if CGRU_LOCATION or AFANASY_SERVER present.
    Safe to call multiple times.
    """
    # Add CGRU_LOCATION to sys.path if provided
    cg_location = CGRU_LOCATION
    if cg_location:
        # Try multiple common subpath layouts to support various installs
        candidate_names = [
            'afanasy',
            os.path.join('afanasy', 'python'),
            'cgru_python',
            'lib_python',
            os.path.join('lib', 'python'),
            'python',
            'lib'
        ]
        candidates = [os.path.join(cg_location, name) for name in candidate_names]
        # Also consider the CGRU_LOCATION itself
        candidates.insert(0, cg_location)
        for p in candidates:
            if p and p not in sys.path and os.path.exists(p):
                sys.path.insert(0, p)
        os.environ['CGRU_LOCATION'] = cg_location

    # Try to set cgruconfig vars for Afanasy server
    try:
        import cgruconfig
        if AFANASY_SERVER:
            try:
                cgruconfig.VARS['af_servername'] = AFANASY_SERVER
            except Exception:
                pass
        if AFANASY_PORT:
            try:
                cgruconfig.VARS['af_serverport'] = int(AFANASY_PORT)
            except Exception:
                pass
    except Exception:
        # cgru not available on this environment; that's fine for offline edits
        logger.debug('cgruconfig not available; skipping CGRU init')


# Convenience function to ensure settings are loaded and CGRU is initialized
def ensure():
    init_cgru()


# Run init on import (safe no-op if not configured)
init_cgru()
