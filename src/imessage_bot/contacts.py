"""Import names and addresses from macOS Contacts into private local storage."""
import json
import os
import plistlib
import subprocess
import time
import uuid
from pathlib import Path

from . import config


def index_contacts(records):
    index = {}
    for record in records:
        name = record.get('name','').strip()
        if not name:
            continue
        for address in record.get('addresses',[]):
            try:
                key=config.normalize(address)
            except (ValueError,AttributeError):
                continue
            index.setdefault(key,name)
    return index


def snapshot():
    path=config.HOME/'contacts.json'
    result=json.loads(path.read_text()) if path.exists() else dict(stage='not_connected',count=0,index={})
    if result.get('stage')=='requesting':
        try:
            os.kill(result['pid'],0)
        except (OSError,KeyError):
            result.update(stage='permission_needed',error='Allow Contacts access for the app running Inbox in macOS Settings → Privacy & Security → Contacts.')
    return result


def save(**values):
    config.private_dir()
    path=config.HOME/'contacts.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(updated=time.time(),**values),ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


def native_sync():
    """Use an app with a usage description when Python has no bundle identity."""
    folder=config.HOME/'Inbox Contacts.app'
    executable=folder/'Contents/MacOS/InboxContacts'
    source=Path(__file__).parent/'native/ContactsSync.swift'
    if not executable.exists() or source.stat().st_mtime>executable.stat().st_mtime:
        executable.parent.mkdir(parents=True,exist_ok=True)
        info=dict(CFBundleIdentifier='local.inbox.contacts',CFBundleName='Inbox Contacts',
                  CFBundleDisplayName='Inbox Contacts',CFBundleExecutable='InboxContacts',
                  CFBundleVersion='1',CFBundlePackageType='APPL',LSUIElement=True,
                  NSContactsUsageDescription='Show contact names beside your conversations in your local Inbox. Your contacts stay on this Mac.')
        with (folder/'Contents/Info.plist').open('wb') as stream:
            plistlib.dump(info,stream)
        subprocess.run(['/usr/bin/xcrun','swiftc',str(source),'-o',str(executable),'-framework','Contacts','-framework','AppKit'],check=True)
    # Finder metadata on generated app bundles is not accepted by codesign.
    # Strip it only from our own helper, and retry signing after a failed build.
    subprocess.run(['/usr/bin/xattr','-cr',str(folder)],check=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier','local.inbox.contacts',str(folder)],check=True)
    result=config.HOME/('contacts-import-'+uuid.uuid4().hex+'.json')
    try:
        subprocess.run(['/usr/bin/open','-W','-n',str(folder),'--args',str(result)],check=True,timeout=120)
        return json.loads(result.read_text())
    finally:
        result.unlink(missing_ok=True)


def sync(request=False):
    previous=snapshot()
    def report(**values):
        save(index=previous.get('index',{}),count=previous.get('count',0),**values)
    try:
        from Contacts import (CNContactStore,CNEntityTypeContacts,CNContactFetchRequest,
            CNContactGivenNameKey,CNContactFamilyNameKey,CNContactMiddleNameKey,
            CNContactOrganizationNameKey,CNContactNicknameKey,CNContactPhoneNumbersKey,CNContactEmailAddressesKey)
        store=CNContactStore.alloc().init()
        authorization=CNContactStore.authorizationStatusForEntityType_(CNEntityTypeContacts)
        if authorization==0 and request:
            report(stage='requesting',pid=os.getpid())
            result=native_sync()
            if result.get('stage')=='connected':
                records=result['records']
                save(stage='connected',count=len(records),index=index_contacts(records),limited=result.get('limited',False))
            else:
                report(stage='permission_needed',error='Allow Inbox Contacts in macOS Settings → Privacy & Security → Contacts, then retry.')
            return
        if authorization not in (3,4):
            report(stage='permission_needed',error='Allow Contacts access for the app running Inbox in macOS Settings → Privacy & Security → Contacts.')
            return
        keys=[CNContactGivenNameKey,CNContactMiddleNameKey,CNContactFamilyNameKey,
              CNContactOrganizationNameKey,CNContactNicknameKey,CNContactPhoneNumbersKey,CNContactEmailAddressesKey]
        fetch=CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
        records=[]
        def collect(contact,stop):
            full=' '.join(str(x) for x in (contact.givenName(),contact.middleName(),contact.familyName()) if x)
            name=full or str(contact.nickname() or contact.organizationName() or '')
            addresses=[str(x.value().stringValue()) for x in contact.phoneNumbers()]
            addresses.extend(str(x.value()) for x in contact.emailAddresses())
            records.append(dict(name=name,addresses=addresses))
        success,error=store.enumerateContactsWithFetchRequest_error_usingBlock_(fetch,None,collect)
        if not success:
            raise RuntimeError('Contacts could not be read.')
        save(stage='connected',count=len(records),index=index_contacts(records),limited=authorization==4)
    except Exception as exc:
        report(stage='error',error='Could not read Contacts. Check permission and sync again.',error_type=type(exc).__name__)


if __name__=='__main__':
    import sys
    sync(request='--request' in sys.argv)
