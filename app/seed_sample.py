"""Sample: Knowsoft Church — hierarchy, sub-admins, members, weekly stats."""
from datetime import date, timedelta
from sqlmodel import Session, select
from app.models import (
    User, UserRole, ChurchUnit, ChurchLevel, ChurchMember, WeeklyStat, SpecialProgram
)
from app.auth import get_password_hash

FIRST = ["David", "Grace", "Samuel", "Ruth", "Michael", "Esther", "Daniel", "Hannah",
         "Joseph", "Mary", "Peter", "Sarah", "James", "Joy", "Emmanuel", "Faith",
         "Caleb", "Blessing", "Isaac", "Deborah", "Timothy", "Peace", "Paul", "Hope"]
LAST = ["Okonkwo", "Adeyemi", "Bello", "Okoro", "Mensah", "Nwachukwu", "Ibrahim",
        "Okafor", "Eze", "Chukwu", "Abdullahi", "Ogunleye", "Adebayo", "Nwosu"]

SAMPLE_PASSWORD = "Church@12345"
DATA_PASSWORD = "Data@12345"


def _ensure_admin(session: Session, email: str, name: str, church_id: int,
                  role=UserRole.church_admin, password: str = SAMPLE_PASSWORD, stats: bool = False):
    """Create or reset sample sub-admin so login always works."""
    u = session.exec(select(User).where(User.email == email)).first()
    if u:
        u.hashed_password = get_password_hash(password)
        u.full_name = name
        u.role = role
        u.church_id = church_id
        u.is_active = True
        u.can_enter_stats = stats
        u.can_create_churches = True
        u.can_approve_members = True
        session.add(u)
    else:
        session.add(User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name=name,
            role=role,
            church_id=church_id,
            is_active=True,
            can_enter_stats=stats,
            can_create_churches=True,
            can_approve_members=True,
        ))
    session.commit()



def _ensure_district_sample_data(session: Session, district, global_c, country, state, group):
    """Ensure Allen district has members, 12 weeks of stats, and a sample program."""
    import itertools
    if not district:
        return
    gcid = global_c.id if global_c else None
    ccid = country.id if country else None
    scid = state.id if state else None
    grid = group.id if group else None

    existing_emails = {
        m.email for m in session.exec(
            select(ChurchMember).where(ChurchMember.church_id == district.id)
        ).all() if m.email
    }
    statuses = [
        ("member", None, None), ("member", None, None), ("member", None, None),
        ("worker", "usher", None), ("worker", "choir", None), ("worker", "prayer", None),
        ("worker", "evangelist", None), ("worker", "media", None),
        ("leader", None, "coordinator"), ("leader", None, "women_leader"),
        ("leader", None, "children_leader"), ("leader", None, "bible_study_teacher"),
        ("pastor", None, "group_pastor"),
    ]
    sexes = ["brother", "sister"]
    ages = ["child", "youth", "campus", "adult"]
    conf = ["saved", "saved", "saved", "restored", "backslidden"]
    added = 0
    target = 80
    mcount = len(session.exec(select(ChurchMember).where(ChurchMember.church_id == district.id)).all())
    if mcount < target:
        for i, (fn, ln) in enumerate(itertools.product(FIRST, LAST)):
            if mcount + added >= target:
                break
            email = f"member{i}@knowsoftchurch.sample"
            if email in existing_emails:
                continue
            st, wt, lt = statuses[i % len(statuses)]
            session.add(ChurchMember(
                church_id=district.id,
                global_church_id=gcid,
                country_church_id=ccid,
                state_church_id=scid,
                group_church_id=grid,
                full_name=f"{fn} {ln}",
                sex=sexes[i % 2],
                age_category=ages[i % 4],
                confession=conf[i % 5],
                member_since=date.today() - timedelta(days=30 * (i % 24)),
                whatsapp=f"+23480{1000000 + i}",
                phone=f"+23480{1000000 + i}",
                email=email,
                address=f"{10 + i} Sample Street, Ikeja, Lagos",
                status=st, worker_type=wt, leader_type=lt,
                approval_status="approved", is_active=True,
            ))
            added += 1
        session.commit()
        print(f"✅ District sample members: added {added} (total target {target})")
    else:
        print(f"ℹ️ District already has {mcount} members")

    scount = len(session.exec(select(WeeklyStat).where(WeeklyStat.church_id == district.id)).all())
    if scount < 12:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        existing_weeks = {
            str(s.week_start) for s in session.exec(
                select(WeeklyStat).where(WeeklyStat.church_id == district.id)
            ).all()
        }
        for w in range(12):
            ws = monday - timedelta(weeks=11 - w)
            if str(ws) in existing_weeks:
                continue
            base = 40 + (w % 5) * 3
            session.add(WeeklyStat(
                church_id=district.id, week_start=ws,
                adult_male=base, adult_female=base + 12,
                children_boys=10 + w % 4, children_girls=12 + w % 3,
                youth_male=8 + w % 5, youth_female=9 + w % 4,
                offering=50000 + w * 2500, tithe=120000 + w * 5000, donation=15000 + w * 1000,
                newcomers=4 + w % 3, converts=2 + w % 2,
                counseling=2 + w % 3, members_in_need=3 + w % 4,
                notes="Knowsoft Church Allen – sample week",
            ))
        session.commit()
        print("✅ District weekly stats ensured (12 weeks)")
    else:
        print(f"ℹ️ District already has {scount} weekly stats")

    prog = session.exec(
        select(SpecialProgram).where(
            SpecialProgram.church_id == district.id,
            SpecialProgram.title == "Thanksgiving & Dedication Service",
        )
    ).first()
    if not prog:
        session.add(SpecialProgram(
            church_id=district.id,
            title="Thanksgiving & Dedication Service",
            description="Special thanksgiving service. All Ikeja Group members invited.",
            program_date=date.today() + timedelta(days=7),
            location="Allen Avenue Auditorium",
            broadcast_to="group",
            is_active=True,
            featured_on_home=False,
        ))
        session.commit()
        print("✅ District sample program created")

    district.approval_status = "approved"
    district.is_active = True
    district.address = district.address or "12 Allen Avenue, Ikeja, Lagos"
    district.resident_pastor = district.resident_pastor or "Pastor Ruth Okoro"
    if getattr(district, "latitude", None) is None:
        district.latitude = 6.6018
    if getattr(district, "longitude", None) is None:
        district.longitude = 3.3515
    district.country_name = district.country_name or "Nigeria"
    district.state_name = district.state_name or "Lagos"
    session.add(district)
    session.commit()


def seed_knowsoft_bible_church(session: Session) -> None:
    try:
        global_c = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-GLOBAL")).first()

        if not global_c:
            def unit(**kw):
                c = ChurchUnit(**kw)
                session.add(c)
                session.commit()
                session.refresh(c)
                return c

            global_c = unit(
                code="KC-GLOBAL", name="Knowsoft Church", level=ChurchLevel.global_church,
                global_code="KC-GLOBAL",
                doctrine="Scripture-based faith, salvation in Christ, discipleship and mission.",
                activity_days="Sunday, Wednesday, Friday",
                owner_name="Apostle David Knowsoft", resident_pastor="Apostle David Knowsoft",
                address="Knowsoft Global HQ, Abuja", phone="+234-800-100-0001",
                email="global@knowsoftchurch.org", country_name="Nigeria",
                approval_status="approved", is_active=True,
            )
            country = unit(
                code="KC-NG", name="Knowsoft Church – Nigeria", level=ChurchLevel.country,
                parent_id=global_c.id, global_code="KC-GLOBAL", country_code="KC-NG",
                country_name="Nigeria", resident_pastor="Rev. Samuel Okonkwo",
                email="nigeria@knowsoftchurch.org", approval_status="approved", is_active=True,
            )
            state = unit(
                code="KC-NG-LAG", name="Knowsoft Church – Lagos State", level=ChurchLevel.state,
                parent_id=country.id, global_code="KC-GLOBAL", country_code="KC-NG",
                state_code="KC-NG-LAG", country_name="Nigeria", state_name="Lagos",
                resident_pastor="Pastor Grace Adeyemi", email="lagos@knowsoftchurch.org",
                approval_status="approved", is_active=True,
            )
            group = unit(
                code="KC-NG-LAG-IKE", name="Knowsoft Church – Ikeja Group", level=ChurchLevel.group,
                parent_id=state.id, global_code="KC-GLOBAL", country_code="KC-NG",
                state_code="KC-NG-LAG", group_code="KC-NG-LAG-IKE",
                country_name="Nigeria", state_name="Lagos",
                resident_pastor="Pastor Michael Bello", email="ikeja@knowsoftchurch.org",
                approval_status="approved", is_active=True,
            )
            district = unit(
                code="KC-NG-LAG-IKE-ALLEN", name="Knowsoft Church – Allen Avenue District",
                level=ChurchLevel.district, parent_id=group.id,
                global_code="KC-GLOBAL", country_code="KC-NG", state_code="KC-NG-LAG",
                group_code="KC-NG-LAG-IKE", district_code="KC-NG-LAG-IKE-ALLEN",
                country_name="Nigeria", state_name="Lagos",
                doctrine=global_c.doctrine, activity_days="Sunday, Wednesday, Friday",
                owner_name="Apostle David Knowsoft", resident_pastor="Pastor Ruth Okoro",
                address="12 Allen Avenue, Ikeja, Lagos", phone="+234-801-234-5678",
                email="allen@knowsoftchurch.org", approval_status="approved", is_active=True,
            )

            # Members
            import itertools
            statuses = [
                ("member", None, None), ("member", None, None), ("member", None, None),
                ("worker", "usher", None), ("worker", "choir", None), ("worker", "prayer", None),
                ("worker", "evangelist", None), ("worker", "media", None),
                ("leader", None, "coordinator"), ("leader", None, "women_leader"),
                ("leader", None, "children_leader"), ("leader", None, "bible_study_teacher"),
                ("pastor", None, "group_pastor"),
            ]
            sexes = ["brother", "sister"]
            ages = ["child", "youth", "campus", "adult"]
            conf = ["saved", "saved", "saved", "restored", "backslidden"]
            n = 0
            for i, (fn, ln) in enumerate(itertools.product(FIRST, LAST)):
                if n >= 80:
                    break
                st, wt, lt = statuses[i % len(statuses)]
                session.add(ChurchMember(
                    church_id=district.id,
                    global_church_id=global_c.id,
                    country_church_id=country.id,
                    state_church_id=state.id,
                    group_church_id=group.id,
                    full_name=f"{fn} {ln}",
                    sex=sexes[i % 2],
                    age_category=ages[i % 4],
                    confession=conf[i % 5],
                    member_since=date.today() - timedelta(days=30 * (i % 24)),
                    whatsapp=f"+23480{1000000 + i}",
                    phone=f"+23480{1000000 + i}",
                    email=f"member{i}@knowsoftchurch.sample",
                    address=f"{10 + i} Sample Street, Ikeja, Lagos",
                    status=st, worker_type=wt, leader_type=lt,
                    approval_status="approved", is_active=True,
                ))
                n += 1
            session.commit()

            today = date.today()
            monday = today - timedelta(days=today.weekday())
            for w in range(12):
                ws = monday - timedelta(weeks=11 - w)
                base = 40 + (w % 5) * 3
                session.add(WeeklyStat(
                    church_id=district.id, week_start=ws,
                    adult_male=base, adult_female=base + 12,
                    children_boys=10 + w % 4, children_girls=12 + w % 3,
                    youth_male=15 + w % 5, youth_female=18 + w % 4,
                    offering=80000 + w * 3000, tithe=110000 + w * 4000, donation=15000 + w * 1000,
                    special_program_attendance=70 + w * 5, newcomers=3 + w % 4, converts=1 + w % 3,
                    counseling=2 + w % 3, members_in_need=3 + w % 4,
                    notes="Knowsoft Church Allen – sample week",
                ))
            session.add(SpecialProgram(
                church_id=district.id,
                title="Victory Sunday Thanksgiving",
                description="Special thanksgiving service. All Ikeja Group members invited.",
                program_date=date.today() + timedelta(days=7),
                location="Allen Avenue Auditorium",
                # featured only after general admin approval - leave false on district
                broadcast_to="group",
                is_active=True,
            ))
            session.commit()
            print(f"✅ Knowsoft Church hierarchy + {n} members + 12 weeks stats created")
        else:
            country = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG")).first()
            state = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG")).first()
            group = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG-IKE")).first()
            district = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG-IKE-ALLEN")).first()
            print("ℹ️ Knowsoft Church units already present – ensuring sub-admin logins")

        # Always ensure sub-admin accounts (even if hierarchy already existed)
        district = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG-IKE-ALLEN")).first()
        group = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG-IKE")).first()
        state = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG-LAG")).first()
        country = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-NG")).first()
        global_c = session.exec(select(ChurchUnit).where(ChurchUnit.code == "KC-GLOBAL")).first()

        if global_c:
            _ensure_admin(session, "global@knowsoftchurch.org", "Apostle David Knowsoft", global_c.id)
        if country:
            _ensure_admin(session, "nigeria@knowsoftchurch.org", "Rev. Samuel Okonkwo", country.id)
        if state:
            _ensure_admin(session, "lagos@knowsoftchurch.org", "Pastor Grace Adeyemi", state.id)
        if group:
            _ensure_admin(session, "ikeja@knowsoftchurch.org", "Pastor Michael Bello", group.id)
        if district:
            _ensure_admin(session, "allen@knowsoftchurch.org", "Pastor Ruth Okoro", district.id)
            _ensure_admin(session, "data@allen.knowsoftchurch.org", "Bro. James Data Officer",
                          district.id, UserRole.data_officer, DATA_PASSWORD, stats=True)
            # Always ensure rich district sample data (members, stats, program)
            _ensure_district_sample_data(session, district, global_c, country, state, group)

        
            # Global showcase program (General Admin can also toggle featured_on_home)
            existing_global_prog = session.exec(
                select(SpecialProgram).where(SpecialProgram.title == "Knowsoft Global Convention")
            ).first()
            if not existing_global_prog and global_c:
                from datetime import date, timedelta
                gp = SpecialProgram(
                    church_id=global_c.id,
                    title="Knowsoft Global Convention",
                    description="Annual gathering of assemblies — worship, teaching and fellowship.",
                    program_date=date.today() + timedelta(days=45),
                    location="International Conference Centre",
                    broadcast_to="global",
                    is_active=True,
                    request_home_display=True,
                    featured_on_home=False,  # General Admin must approve
                )
                session.add(gp)
                session.commit()
                print("✅ Sample featured Global program for home page")

            print("✅ Sample sub-admin logins ready:")
        print("   allen@knowsoftchurch.org / Church@12345  (District – use this first)")
        print("   ikeja@knowsoftchurch.org / Church@12345")
        print("   lagos@knowsoftchurch.org / Church@12345")
        print("   nigeria@knowsoftchurch.org / Church@12345")
        print("   global@knowsoftchurch.org / Church@12345")
        print("   data@allen.knowsoftchurch.org / Data@12345")
        # Remittance + map coordinates (so global map has markers)
        if district:
            district.tithe_account_name = "Knowsoft Church Allen Tithe"
            district.tithe_account_number = "0123456789"
            district.tithe_bank_name = "Sample Bank"
            district.offering_account_name = "Knowsoft Church Allen Offering"
            district.offering_account_number = "9876543210"
            district.offering_bank_name = "Sample Bank"
            district.pastor_phone = "+234-801-234-5678"
            district.pastor_email = "pastor@knowsoftchurch.org"
            district.weekly_activities_note = "Sunday 8am & 10am · Wednesday Bible study 6pm · Friday prayer 7pm"
            district.latitude = 6.6018
            district.longitude = 3.3515
            district.country_name = district.country_name or "Nigeria"
            district.state_name = district.state_name or "Lagos"
            session.add(district)
        if global_c:
            global_c.latitude = 9.0765
            global_c.longitude = 7.3986
            global_c.country_name = global_c.country_name or "Nigeria"
            session.add(global_c)
        if country:
            country.latitude = 9.0820
            country.longitude = 8.6753
            country.country_name = country.country_name or "Nigeria"
            session.add(country)
        if state:
            state.latitude = 6.5244
            state.longitude = 3.3792
            state.country_name = state.country_name or "Nigeria"
            state.state_name = state.state_name or "Lagos"
            session.add(state)
        if group:
            group.latitude = 6.6018
            group.longitude = 3.3515
            group.country_name = group.country_name or "Nigeria"
            group.state_name = group.state_name or "Lagos"
            session.add(group)
        session.commit()
        print("✅ Map coordinates set on sample churches")
    except Exception as e:
        session.rollback()
        print(f"⚠️ Sample seed error: {e}")
        import traceback
        traceback.print_exc()
