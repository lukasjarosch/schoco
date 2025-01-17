from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Path
import auth
import database_config
import models_and_schemas
import crud
import cookies_api
import uuid
import os
import git
from sqlmodel import Session

code = APIRouter(prefix="/api")


@code.post('/createNewHelloWorld', dependencies=[Depends(auth.oauth2_scheme)])
def createNewHelloWorld(newProject: models_and_schemas.newProject, db: Session = Depends(database_config.get_db), username=Depends(auth.get_username_by_token)):

    project_uuid = str(uuid.uuid4())
    user = crud.get_user_by_username(db=db, username=username)
    project = models_and_schemas.Project(
        name=newProject.projectName, description=newProject.projectDescription, uuid=project_uuid, owner_id=user.id)

    # create git repo
    if not git.create_repo(project_uuid):
        raise HTTPException(status_code=500, detail="Could not create project")

    # load the template files into the git repo
    for file in os.listdir("./java_helloWorld"):
        if file.endswith(".class"):
            continue
        file_content = open(file=f"./java_helloWorld/{file}", mode="rb").read()
        if not git.add_file(project_uuid=project_uuid,
                            file_name=file, file_content=file_content):
            git.remove_repo(project_uuid=project_uuid)
            raise HTTPException(
                status_code=500, detail="Could not create project")

    # create project-representation in DB
    if not crud.create_project(db=db, project=project):
        # delete git repo
        git.remove_repo(project_uuid=project_uuid)

        raise HTTPException(
            status_code=500, detail="Could not create project")

    return project_uuid


def project_access_allowed(project_uuid: str, db: Session = Depends(database_config.get_db), username=Depends(auth.get_username_by_token)):

    # teacher is allowed to open
    user = crud.get_user_by_username(db=db, username=username)
    if user.role == "teacher":
        return True

    # otherwise: user must be owner
    project = crud.get_project_by_project_uuid(
        db=db, project_uuid=project_uuid)
    if project.owner.username == username:
        return True

    raise HTTPException(
        status_code=405, detail="You're not allowed to open or edit this project")


def project_access_allowed_teacher_only(project_uuid: str, db: Session = Depends(database_config.get_db), username=Depends(auth.get_username_by_token)):

    # user must be teacher
    user = crud.get_user_by_username(db=db, username=username)
    if user.role != "teacher":
        raise HTTPException(
            status_code=405, detail="You're not allowed to open or edit this project")

    # user must be owner
    project = crud.get_project_by_project_uuid(
        db=db, project_uuid=project_uuid)
    if project.owner.username == username:
        return True

    raise HTTPException(
        status_code=405, detail="You're not allowed to open or edit this project")


results = []


def recursively_download_all_files(project_uuid: str, path: str):
    global results

    root = git.load_all_meta_content(
        project_uuid=project_uuid, path=path)

    # download all files
    for c in root:
        if not c['isDir']:
            print(c)
            results.append({'path': c['path'], 'content': git.download_file_by_url(
                url=git.replace_base_url(c['download_url'])), 'sha': c['sha']})
        else:
            recursively_download_all_files(
                project_uuid=project_uuid, path=f"/{c['path']}/")

    return (results)


@ code.get('/loadAllFiles/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def loadAllFiles(project_uuid: str = Path()):
    global results
    results = []

    res = recursively_download_all_files(project_uuid=project_uuid, path="/")

    return res


@ code.post('/updateDescription/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def saveDescription(updateDescription: models_and_schemas.updateDescription, project_uuid: str = Path(), db: Session = Depends(database_config.get_db)):
    result = crud.updateDescription(
        db=db, project_uuid=project_uuid, description=updateDescription.description)
    return result


@ code.get('/getProjectInfo/{project_uuid}', dependencies=[Depends(auth.oauth2_scheme)])
def getProjectName(project_uuid: str = Path(), db: Session = Depends(database_config.get_db)):
    project = crud.get_project_by_project_uuid(
        db=db, project_uuid=project_uuid)
    return {"name": project.name, "description": project.description}


@ code.post('/saveFileChanges/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def saveFileChanges(fileChanges: models_and_schemas.FileChangesList, project_uuid: str = Path()):

    success = []
    for f in fileChanges.changes:
        res = git.update_file(project_uuid, f.path, f.content, f.sha)
        if res:
            success.append(
                {'path': f.path, 'content': f.content, 'sha': res['sha']})

    return success


@ code.get('/getMyProjects', dependencies=[Depends(auth.oauth2_scheme)])
def getMyProjects(db: Session = Depends(database_config.get_db), username=Depends(auth.get_username_by_token)):
    projects = crud.get_projects_by_username(db=db, username=username)
    return {'projects': projects}


@ code.post('/prepareCompile/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def prepareCompile(prepareCompile: models_and_schemas.prepareCompile, project_uuid: str = Path()):

    # write files to container-mount and return WS-URL
    c = cookies_api.prepareCompile(prepareCompile.files)

    return c


@ code.post('/startCompile/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def startCompile(startCompile: models_and_schemas.startCompile, background_tasks: BackgroundTasks, project_uuid: str = Path()):

    result = cookies_api.startCompile(
        startCompile.container_uuid, startCompile.ip, startCompile.port)

    # save compilation-results (.class-files)
    cookies_api.save_compilation_result(
        startCompile.container_uuid, project_uuid)

    # before return: start background_task to refill new_containers
    background_tasks.add_task(
        cookies_api.kill_n_create, startCompile.container_uuid)

    return result


@ code.get('/prepareExecute/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def prepareExecute(project_uuid: str = Path()):

    # prepares container by copying .class files into container-mount
    # returns {'executable': false} if no files are found
    c = cookies_api.prepare_execute(project_uuid)
    return c


@ code.post('/startExecute/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def startExecute(startExecute: models_and_schemas.startExecute, background_tasks: BackgroundTasks, project_uuid: str = Path()):

    result = cookies_api.start_execute(
        startExecute.container_uuid, startExecute.ip, startExecute.port)

    background_tasks.add_task(
        cookies_api.kill_n_create, startExecute.container_uuid)

    return result


@ code.get('/prepareTest/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def prepareTest(project_uuid: str = Path()):
    # we now use "prepare_execute", since it is (at least until now) the same code
    c = cookies_api.prepare_execute(project_uuid)
    return c


@ code.post('/startTest/{project_uuid}', dependencies=[Depends(project_access_allowed)])
def startTest(startTest: models_and_schemas.startTest, background_tasks: BackgroundTasks, project_uuid: str = Path()):

    result = cookies_api.start_test(
        startTest.container_uuid, startTest.ip, startTest.port)

    background_tasks.add_task(
        cookies_api.kill_n_create, startTest.container_uuid)

    return result


@ code.post('/createHomework/{project_uuid}', dependencies=[Depends(project_access_allowed_teacher_only)])
def createHomework(homework: models_and_schemas.homework, project_uuid: str = Path()):

    orig_project_uuid = project_uuid
    template_project_uuid = str(uuid.uuid4())

    # copy original project to create template for the homework
    if not git.forkProject(
            orig_project_uuid=orig_project_uuid, template_project_uuid=template_project_uuid):
        return False

    # TODO Continue this function...

    return True
