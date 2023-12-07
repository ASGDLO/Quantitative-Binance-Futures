from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from starlette.responses import FileResponse


router_ui = APIRouter()


@router_ui.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse(str(Path(__file__).parent / 'ui/favicon.ico'))


@router_ui.get('/fallback_file.html', include_in_schema=False)
async def fallback():
    return FileResponse(str(Path(__file__).parent / 'ui/fallback_file.html'))


@router_ui.get('/ui_version', include_in_schema=False)
async def ui_version():
    from freqtrade.commands.deploy_commands import read_ui_version
    uibase = Path(__file__).parent / 'ui/installed/'
    version = read_ui_version(uibase)

    return {
        "version": version if version else "not_installed",
    }


def is_relative_to(path, base) -> bool:
    # Helper function simulating behaviour of is_relative_to, which was only added in python 3.9
    try:
        path.relative_to(base)
        return True
    except ValueError:
        pass
    return False


@router_ui.get('/{rest_of_path:path}', include_in_schema=False)
async def index_html(rest_of_path: str, path_service: PathService = Depends(PathService)):
    """
    Serve files for UI, with path fallback to index.html.
    """
    if path_service.is_api_or_hidden_path(rest_of_path):
        raise PathNotFoundException()

    try:
        filename, media_type = path_service.get_file_and_media_type(rest_of_path)

        if path_service.is_valid_file(filename):
            return FileResponse(str(filename), media_type=media_type)

        return path_service.get_index_or_fallback_response()
    except Exception as e:
        logger.error(f"Error serving file: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

