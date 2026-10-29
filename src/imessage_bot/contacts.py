"""Import names and addresses from macOS Contacts into private local storage."""
import json
import os
import threading
import time

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
            done=threading.Event()
            store.requestAccessForEntityType_completionHandler_(CNEntityTypeContacts,lambda granted,error:done.set())
            if not done.wait(60):
                report(stage='permission_needed',error='Respond to the macOS Contacts permission prompt, then sync again.')
                return
            authorization=CNContactStore.authorizationStatusForEntityType_(CNEntityTypeContacts)
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
