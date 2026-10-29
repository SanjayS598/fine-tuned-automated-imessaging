#!/usr/bin/env python3
"""Build the local, self-contained Inbox.app and copy private settings separately.

Run with this project's Python environment. No message history or model weights
go into the app bundle or the public repository. The base model and adapter are copied
to the user's private Application Support directory for fully local inference.
"""
import argparse
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
import tempfile
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from imessage_bot import config
from imessage_bot.policy import owner_adapter


def copy_directory(source,destination):
    subprocess.run(['/usr/bin/ditto','--norsrc',str(source),str(destination)],check=True)


def engine_update(data_home):
    target=data_home/'engine'
    stage=data_home/('engine-stage-'+str(time.time_ns()))
    shutil.copytree(ROOT/'src',stage/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    previous=None
    if target.exists():
        previous=data_home/('engine-previous-'+str(time.time_ns()))
        target.rename(previous)
    try:
        stage.rename(target)
    except BaseException:
        if previous:previous.rename(target)
        raise
    if previous:shutil.rmtree(previous)


def wrapper_fingerprint():
    digest=hashlib.sha256()
    for path in (ROOT/'macos/Inbox.swift',ROOT/'macos/MakeIcon.swift',ROOT/'uv.lock'):
        digest.update(path.read_bytes())
    digest.update(sys.version.encode())
    return digest.hexdigest()


def prepare_private_data(destination):
    """Initial import is explicit and idempotent; never replace existing settings."""
    if (destination/'config.json').exists():
        existing=json.loads((destination/'config.json').read_text())
        if not owner_adapter(existing):
            raise ValueError('Existing app data has no owner-only model. It was preserved; resolve it before packaging.')
        return
    cfg=config.load()
    if not owner_adapter(cfg):
        raise ValueError('Finish owner-only training before packaging the personal app.')
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination.chmod(0o700)
    from huggingface_hub import snapshot_download
    model=Path(snapshot_download(cfg['model'],local_files_only=True,
        allow_patterns=['*.json','*.safetensors','*.model','*.txt','*.tiktoken','*.jinja']))
    target_model=destination/'models'/'personal-base'
    if not target_model.exists():
        # HF cache entries are symlinks. Dereference them to make this standalone.
        shutil.copytree(model,target_model,symlinks=False)
    adapter=destination/'adapters'/'owner-only'
    adapter.mkdir(parents=True,exist_ok=True)
    old_adapter=Path(cfg['adapter_path'])
    for name in ('adapter_config.json','adapters.safetensors','personal-model.json'):
        shutil.copy2(old_adapter/name,adapter/name)
    metadata=json.loads((adapter/'personal-model.json').read_text())
    metadata.update(source_model=cfg['model'],model=str(target_model))
    (adapter/'personal-model.json').write_text(json.dumps(metadata,indent=2))
    for name in ('contacts.json','training-status.json'):
        if (config.HOME/name).exists():
            shutil.copy2(config.HOME/name,destination/name)
    contact_app=config.HOME/'Inbox Contacts.app'
    if contact_app.exists():
        copy_directory(contact_app,destination/contact_app.name)
    state=config.HOME/'state.sqlite3'
    if state.exists():
        with sqlite3.connect(state.as_uri()+'?mode=ro',uri=True) as source:
            with sqlite3.connect(destination/state.name) as target:
                source.backup(target)
    cfg.update(model=str(target_model),adapter_path=str(adapter),enabled=False,dry_run=True,
               packaged_data=True,revision=cfg.get('revision',0)+1)
    # Write configuration last so an interrupted first import can be retried.
    temporary=destination/'config.json.tmp'
    temporary.write_text(json.dumps(cfg,indent=2)+'\n')
    temporary.chmod(0o600)
    temporary.replace(destination/'config.json')


def make_icon(resources,build):
    executable=build/'MakeIcon'
    subprocess.run(['/usr/bin/xcrun','swiftc',str(ROOT/'macos/MakeIcon.swift'),'-o',str(executable)],check=True)
    png=build/'Inbox.png'
    subprocess.run([str(executable),str(png)],check=True)
    iconset=build/'Inbox.iconset'
    iconset.mkdir()
    for size in (16,32,128,256,512):
        for scale in (1,2):
            suffix='@2x' if scale==2 else ''
            subprocess.run(['/usr/bin/sips','-z',str(size*scale),str(size*scale),str(png),
                            '--out',str(iconset/f'icon_{size}x{size}{suffix}.png')],check=True,capture_output=True)
    subprocess.run(['/usr/bin/iconutil','-c','icns',str(iconset),'-o',str(resources/'Inbox.icns')],check=True)


def build(output,data_home,replace=False):
    if sys.platform!='darwin':
        raise ValueError('This builder requires macOS on Apple Silicon.')
    if sys.version_info[:2]!=(3,12):
        raise ValueError('Build with the project Python 3.12 environment.')
    fingerprint=wrapper_fingerprint()
    if output.exists():
        if not replace:
            raise ValueError(f'{output} already exists. Quit Inbox and use --replace to update it.')
        with (output/'Contents/Info.plist').open('rb') as stream:
            installed_info=plistlib.load(stream)
            if installed_info.get('CFBundleIdentifier')!='local.personal-inbox.app':
                raise ValueError('The existing app is not Inbox. It was preserved.')
        if installed_info.get('InboxWrapperFingerprint')==fingerprint:
            prepare_private_data(data_home)
            engine_update(data_home)
            subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(output)],check=True)
            print('Updated the private engine and UI. The signed launcher and its macOS permission identity are unchanged.',flush=True)
            return
    # Desktop can be backed by a File Provider that immediately restores Finder
    # attributes. Build/sign outside it and install the executable locally.
    build_folder=Path(tempfile.mkdtemp(prefix='inbox-macos-build-'))
    bundle=build_folder/'Inbox.app'
    contents=bundle/'Contents'
    resources=contents/'Resources'
    binary=contents/'MacOS'/'Inbox'
    binary.parent.mkdir(parents=True)
    resources.mkdir()
    print('Copying Python runtime and installed dependencies…',flush=True)
    runtime=resources/'Python'
    copy_directory(Path(sys.base_prefix),runtime)
    site_packages=runtime/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    source_site=Path(sys.prefix)/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    shutil.copytree(source_site,site_packages,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc','_editable*','_virtualenv*',
                                                 'direct_url.json','uv_build.json','uv_cache.json','.DS_Store'))
    source=resources/'app'/'src'
    shutil.copytree(ROOT/'src',source,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    info=dict(CFBundleName='Inbox',CFBundleDisplayName='Inbox',CFBundleIdentifier='local.personal-inbox.app',
              CFBundleExecutable='Inbox',CFBundlePackageType='APPL',CFBundleVersion='1',
              CFBundleShortVersionString='1.0',CFBundleIconFile='Inbox.icns',LSMinimumSystemVersion='13.0',
              LSApplicationCategoryType='public.app-category.productivity',NSHighResolutionCapable=True,
              NSContactsUsageDescription='Show your contact names in your private local inbox.',
              NSAppleEventsUsageDescription='Send replies through Messages only for conversations you enable.',
              NSAppTransportSecurity=dict(NSAllowsLocalNetworking=True,NSAllowsArbitraryLoadsInWebContent=True))
    info['InboxWrapperFingerprint']=fingerprint
    with (contents/'Info.plist').open('wb') as stream:
        plistlib.dump(info,stream)
    print('Building the native window and app icon…',flush=True)
    subprocess.run(['/usr/bin/xcrun','swiftc',str(ROOT/'macos/Inbox.swift'),'-o',str(binary),
                    '-framework','AppKit','-framework','WebKit','-O'],check=True)
    make_icon(resources,build_folder)
    print('Copying your trained model and settings into private Application Support…',flush=True)
    prepare_private_data(data_home)
    engine_update(data_home)
    env=dict(os.environ,PYTHONHOME=str(runtime),PYTHONPATH=str(source),PYTHONNOUSERSITE='1',
             PYTHONDONTWRITEBYTECODE='1',IMESSAGE_BOT_HOME=str(data_home),HF_HUB_OFFLINE='1')
    print('Checking the bundled runtime, model and native libraries…',flush=True)
    subprocess.run([str(runtime/'bin/python3.12'),'-c',
        'import mlx.core, Foundation, Contacts; from imessage_bot.config import load; from imessage_bot.policy import owner_adapter; assert owner_adapter(load()); print("Bundled runtime and owner-only model: ready")'],env=env,check=True)
    subprocess.run(['/usr/bin/xattr','-cr',str(bundle)],check=True)
    print('Signing and verifying the app…',flush=True)
    subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-','--identifier','local.personal-inbox.app',str(bundle)],check=True)
    subprocess.run(['/usr/bin/xattr','-cr',str(bundle)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],check=True)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():
        backup=data_home/'app-backups'/time.strftime('%Y%m%d-%H%M%S')/'Inbox.app'
        backup.parent.mkdir(parents=True,exist_ok=False)
        shutil.move(output,backup)
    copy_directory(bundle,output)
    subprocess.run(['/usr/bin/xattr','-cr',str(output)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(output)],check=True)
    print(f'Created {output}',flush=True)
    desktop=Path.home()/'Desktop/Inbox.app'
    if not desktop.exists() and not desktop.is_symlink():
        desktop.symlink_to(output,target_is_directory=True)
        print(f'Desktop shortcut: {desktop}',flush=True)
    shutil.rmtree(build_folder)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Build the personal macOS Inbox app.')
    parser.add_argument('--output',type=Path,default=Path.home()/'Applications/Inbox.app')
    parser.add_argument('--replace',action='store_true',help='Update an installed Inbox app after quitting it; preserve a backup.')
    args=parser.parse_args()
    build(args.output.expanduser().resolve(),Path.home()/'Library/Application Support/Personal Inbox',replace=args.replace)
