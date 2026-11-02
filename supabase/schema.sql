-- LearnHub – Supabase schema. Run once in Supabase → SQL Editor.
-- Security model: RLS on every table; answer keys live in a table no student can read;
-- grading, access checks and reports run in SECURITY DEFINER functions; payments are
-- written only by the server (service role) after the gateway confirms them.

-- ============ tables ============
create table public.schools(
  id bigint generated always as identity primary key,
  name text not null, created_at timestamptz not null default now());

create table public.profiles(
  id uuid primary key references auth.users on delete cascade,
  name text not null default '',
  board text check (board in ('NCERT/CBSE','ICSE','IGCSE','State Board')),
  grade int check (grade between 1 and 10),
  role text not null default 'student' check (role in ('student','admin')),
  school_id bigint references public.schools,
  active boolean not null default true,
  created_at timestamptz not null default now());

create table public.courses(
  id bigint generated always as identity primary key,
  title text not null, description text default '',
  board text, grade int, price_paise int not null default 0 check (price_paise >= 0),
  validity_days int not null default 365, published boolean not null default false,
  created_at timestamptz not null default now());

create table public.tests(
  id bigint generated always as identity primary key,
  title text not null, board text not null, grade int not null check (grade between 1 and 10),
  subject text not null check (subject in ('Mathematics','Science','English')),
  kind text not null default 'practice' check (kind in ('practice','topic','past_paper')),
  year int, duration_min int not null default 30 check (duration_min > 0),
  course_id bigint references public.courses,
  is_free boolean not null default false,        -- free samples to hook students
  published boolean not null default true,
  created_at timestamptz not null default now());

create table public.questions(
  id bigint generated always as identity primary key,
  board text not null, grade int not null, subject text not null, topic text default '',
  qtype text not null check (qtype in ('mcq','short')),
  text text not null, options jsonb not null default '[]', marks numeric not null default 1,
  source text not null default 'original', year int,
  created_at timestamptz not null default now());

create table public.question_keys(           -- NEVER readable by students
  question_id bigint primary key references public.questions on delete cascade,
  answer text not null, explanation text default '');

create table public.test_questions(
  test_id bigint references public.tests on delete cascade,
  question_id bigint references public.questions on delete cascade,
  primary key(test_id, question_id));

create table public.purchases(
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users on delete cascade,
  course_id bigint not null references public.courses,
  status text not null default 'created' check (status in ('created','paid','failed','refunded')),
  amount_paise int not null, provider text not null default 'razorpay',
  provider_order_id text unique, provider_payment_id text unique,
  created_at timestamptz not null default now(), paid_at timestamptz, expires_at timestamptz);

create table public.school_licenses(
  id bigint generated always as identity primary key,
  school_id bigint not null references public.schools,
  course_id bigint not null references public.courses,
  seats int not null default 0, expires_at timestamptz not null,
  created_at timestamptz not null default now());

create table public.attempts(
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users on delete cascade,
  test_id bigint not null references public.tests,
  started_at timestamptz not null default now(), submitted_at timestamptz,
  score numeric, total numeric, late boolean not null default false);

create table public.attempt_questions(      -- snapshot of what was served
  attempt_id bigint references public.attempts on delete cascade,
  question_id bigint references public.questions,
  primary key(attempt_id, question_id));

create table public.answers(
  attempt_id bigint references public.attempts on delete cascade,
  question_id bigint references public.questions,
  response text, correct boolean not null, awarded numeric not null,
  primary key(attempt_id, question_id));

create index on public.attempts(user_id, submitted_at);
create index on public.tests(board, grade, subject);
create index on public.purchases(user_id, course_id, status);
create index on public.questions(board, grade, subject);

-- ============ helpers ============
create function public.is_admin() returns boolean language sql stable security definer
set search_path = public as $$
  select exists(select 1 from profiles where id = auth.uid() and role = 'admin' and active) $$;

create function public.has_access(p_test bigint) returns boolean language sql stable security definer
set search_path = public as $$
  select exists(select 1 from tests t
    where t.id = p_test and t.published and exists(select 1 from profiles where id = auth.uid() and active)
    and ( t.is_free or public.is_admin()
      or exists(select 1 from purchases p where p.user_id = auth.uid() and p.course_id = t.course_id
                and p.status = 'paid' and (p.expires_at is null or p.expires_at > now()))
      or exists(select 1 from school_licenses l join profiles pr on pr.school_id = l.school_id
                where pr.id = auth.uid() and l.course_id = t.course_id and l.expires_at > now()))) $$;

create function public.check_answer(p_type text, p_key text, p_resp text) returns boolean
language plpgsql immutable as $$
declare a text; r text := lower(regexp_replace(btrim(coalesce(p_resp,'')), '\s+', ' ', 'g'));
begin
  if r = '' then return false; end if;
  if p_type = 'mcq' then return upper(btrim(p_resp)) = upper(btrim(p_key)); end if;
  foreach a in array string_to_array(p_key, '|') loop
    if lower(regexp_replace(btrim(a), '\s+', ' ', 'g')) = r then return true; end if;
    if btrim(a) ~ '^-?\d+(\.\d+)?$' and btrim(p_resp) ~ '^-?\d+(\.\d+)?$'
       and btrim(a)::numeric = btrim(p_resp)::numeric then return true; end if;
  end loop;
  return false;
end $$;

-- new auth user -> profile (role is NEVER taken from client-supplied metadata)
create function public.handle_new_user() returns trigger language plpgsql security definer
set search_path = public as $$
declare g text := new.raw_user_meta_data->>'grade'; b text := new.raw_user_meta_data->>'board';
begin
  insert into profiles(id, name, board, grade) values (new.id,
    left(coalesce(new.raw_user_meta_data->>'name',''), 100),
    case when b in ('NCERT/CBSE','ICSE','IGCSE','State Board') then b end,
    case when g ~ '^\d+$' and g::int between 1 and 10 then g::int end);
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- students may edit name/board/grade only; role/active/school need an admin (or the SQL editor)
create function public.profiles_guard() returns trigger language plpgsql as $$
begin
  if auth.uid() is not null and not public.is_admin()
     and (new.role <> old.role or new.active <> old.active or new.school_id is distinct from old.school_id or new.id <> old.id) then
    raise exception 'not allowed';
  end if;
  return new;
end $$;
create trigger profiles_guard before update on public.profiles
  for each row execute function public.profiles_guard();

-- ============ exam API (RPC) ============
create function public.start_attempt(p_test bigint) returns jsonb language plpgsql security definer
set search_path = public as $$
declare v_t tests; v_a bigint;
begin
  if not public.has_access(p_test) then raise exception 'locked: purchase required'; end if;
  select * into v_t from tests where id = p_test;
  insert into attempts(user_id, test_id) values (auth.uid(), p_test) returning id into v_a;
  insert into attempt_questions(attempt_id, question_id) select v_a, question_id from test_questions where test_id = p_test;
  return jsonb_build_object('attempt_id', v_a, 'title', v_t.title, 'duration_min', v_t.duration_min,
    'questions', (select coalesce(jsonb_agg(jsonb_build_object('id', q.id, 'text', q.text, 'qtype', q.qtype,
        'options', q.options, 'marks', q.marks, 'topic', q.topic) order by q.id), '[]'::jsonb)
      from questions q join attempt_questions aq on aq.question_id = q.id where aq.attempt_id = v_a));
end $$;

create function public.submit_attempt(p_attempt bigint, p_answers jsonb) returns bigint
language plpgsql security definer set search_path = public as $$
declare v_a attempts; v_t tests; r record; v_resp text; v_ok boolean; v_score numeric := 0; v_total numeric := 0;
begin
  select * into v_a from attempts where id = p_attempt and user_id = auth.uid() for update;
  if not found then raise exception 'no such attempt'; end if;
  if v_a.submitted_at is not null then raise exception 'already submitted'; end if;
  select * into v_t from tests where id = v_a.test_id;
  for r in select q.id, q.qtype, q.marks, k.answer from attempt_questions aq
           join questions q on q.id = aq.question_id join question_keys k on k.question_id = q.id
           where aq.attempt_id = p_attempt loop
    v_resp := left(coalesce(p_answers ->> (r.id::text), ''), 500);
    v_ok := public.check_answer(r.qtype, r.answer, v_resp);
    insert into answers values (p_attempt, r.id, v_resp, v_ok, case when v_ok then r.marks else 0 end);
    v_total := v_total + r.marks; if v_ok then v_score := v_score + r.marks; end if;
  end loop;
  update attempts set submitted_at = now(), score = v_score, total = v_total,
    late = now() > started_at + make_interval(mins => v_t.duration_min) + interval '2 minutes' where id = p_attempt;
  return p_attempt;
end $$;

create function public.get_report(p_attempt bigint) returns jsonb language plpgsql security definer
set search_path = public as $$
declare v_a attempts;
begin
  select * into v_a from attempts where id = p_attempt and (user_id = auth.uid() or public.is_admin());
  if not found or v_a.submitted_at is null then raise exception 'report not available'; end if;
  return jsonb_build_object('attempt', to_jsonb(v_a), 'title', (select title from tests where id = v_a.test_id),
    'items', (select coalesce(jsonb_agg(jsonb_build_object('id', q.id, 'text', q.text, 'topic', q.topic, 'qtype', q.qtype,
        'options', q.options, 'marks', q.marks, 'answer', k.answer, 'explanation', k.explanation,
        'response', an.response, 'correct', an.correct, 'awarded', an.awarded) order by q.id), '[]'::jsonb)
      from attempt_questions aq join questions q on q.id = aq.question_id
      join question_keys k on k.question_id = q.id
      left join answers an on an.attempt_id = aq.attempt_id and an.question_id = q.id
      where aq.attempt_id = p_attempt));
end $$;

create function public.my_topic_stats() returns table(subject text, topic text, got numeric, total numeric)
language sql stable security definer set search_path = public as $$
  select q.subject, coalesce(nullif(q.topic,''),'General'), sum(an.awarded), sum(q.marks)
  from attempts a join answers an on an.attempt_id = a.id join questions q on q.id = an.question_id
  where a.user_id = auth.uid() and a.submitted_at is not null group by 1, 2 $$;

create function public.admin_stats() returns jsonb language plpgsql security definer
set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'not allowed'; end if;
  return jsonb_build_object(
    'students', (select count(*) from profiles where role = 'student'),
    'attempts', (select count(*) from attempts where submitted_at is not null),
    'revenue_paise', (select coalesce(sum(amount_paise), 0) from purchases where status = 'paid'),
    'purchases', (select count(*) from purchases where status = 'paid'));
end $$;

-- catalog for logged-in students (counts + lock state, no content)
create view public.test_catalog as
  select t.id, t.title, t.board, t.grade, t.subject, t.kind, t.year, t.duration_min, t.is_free, t.course_id,
         (select count(*) from test_questions tq where tq.test_id = t.id) as question_count,
         public.has_access(t.id) as unlocked
  from tests t where t.published;

-- ============ row-level security ============
alter table public.schools enable row level security;         alter table public.profiles enable row level security;
alter table public.courses enable row level security;         alter table public.tests enable row level security;
alter table public.questions enable row level security;       alter table public.question_keys enable row level security;
alter table public.test_questions enable row level security;  alter table public.purchases enable row level security;
alter table public.school_licenses enable row level security; alter table public.attempts enable row level security;
alter table public.attempt_questions enable row level security; alter table public.answers enable row level security;

create policy admin_all on public.schools for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.tests for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.questions for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.question_keys for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.test_questions for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.school_licenses for all using (public.is_admin()) with check (public.is_admin());
create policy admin_all on public.courses for all using (public.is_admin()) with check (public.is_admin());
create policy students_read on public.courses for select to authenticated using (published);
create policy own_or_admin_read on public.profiles for select using (id = auth.uid() or public.is_admin());
create policy own_or_admin_update on public.profiles for update using (id = auth.uid() or public.is_admin()) with check (id = auth.uid() or public.is_admin());
create policy admin_delete on public.profiles for delete using (public.is_admin());
create policy own_or_admin_read on public.purchases for select using (user_id = auth.uid() or public.is_admin());   -- writes: service role only
create policy own_or_admin_read on public.attempts for select using (user_id = auth.uid() or public.is_admin());   -- writes: RPC only
create policy own_or_admin_read on public.attempt_questions for select
  using (exists(select 1 from attempts a where a.id = attempt_id and (a.user_id = auth.uid() or public.is_admin())));
create policy own_or_admin_read on public.answers for select
  using (exists(select 1 from attempts a where a.id = attempt_id and (a.user_id = auth.uid() or public.is_admin())));

-- ============ privileges ============
revoke all on all tables in schema public from anon;
revoke insert, delete, truncate on public.profiles, public.attempts, public.attempt_questions, public.answers, public.purchases from authenticated;
revoke update on public.attempts, public.attempt_questions, public.answers, public.purchases from authenticated;
revoke all on public.question_keys from authenticated;   -- admins reach it via RLS policy + a separate grant:
grant select, insert, update, delete on public.question_keys to authenticated;  -- RLS still limits this to admins
grant select on public.test_catalog to authenticated;
revoke execute on all functions in schema public from public, anon;
grant execute on function public.start_attempt(bigint), public.submit_attempt(bigint, jsonb), public.get_report(bigint),
  public.my_topic_stats(), public.admin_stats(), public.has_access(bigint), public.is_admin() to authenticated;
