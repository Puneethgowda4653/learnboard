"""Seeds ORIGINAL sample questions + tests. Not real board papers."""
import json, random, sqlite3
import app as A
random.seed(7)
c = sqlite3.connect(A.DB)
if c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]:
    print("Already seeded"); raise SystemExit
Q = []
def add(b, g, s, t, text, ans, exp, opts=None, m=1):
    Q.append((b, g, s, t, "mcq" if opts else "short", text, json.dumps(opts or []), str(ans), exp, m, "original-sample", None))
def mcq(b, g, s, t, text, correct, wrong, exp):
    o = [correct] + wrong; random.shuffle(o); add(b, g, s, t, text, "ABCD"[o.index(correct)], exp, o)

for b in A.BOARDS:
    for g in range(1, 11):
        for _ in range(4):  # arithmetic scaled by class
            hi = 10 ** min(1 + (g + 1) // 2, 4); x, y = random.randint(2, hi), random.randint(2, hi)
            add(b, g, "Mathematics", "Number operations", f"What is {x} + {y}?", x + y, f"Add the numbers: {x} + {y} = {x+y}.")
        x, y = random.randint(2, 12), random.randint(2, 12)
        add(b, g, "Mathematics", "Multiplication", f"What is {x} × {y}?", x * y, f"{x} × {y} = {x*y}.")
        if g >= 6:
            a = random.randint(2, 9); r = random.randint(1, 15)
            add(b, g, "Mathematics", "Linear equations", f"Solve for x: {a}x + {a} = {a*r + a}", r, f"Subtract {a}: {a}x = {a*r}. Divide by {a}: x = {r}.")
        if g >= 8:
            k = random.choice([(3, 4, 5), (5, 12, 13), (8, 15, 17)])
            add(b, g, "Mathematics", "Pythagoras / Triangles", f"A right triangle has legs {k[0]} and {k[1]}. Find the hypotenuse.", k[2], f"√({k[0]}²+{k[1]}²)=√{k[0]**2+k[1]**2}={k[2]}.")
        if g >= 9:
            p = random.randint(1, 6); q = random.randint(1, 6)
            add(b, g, "Mathematics", "Quadratic equations", f"Sum of the roots of x² − {p+q}x + {p*q} = 0 is?", p + q, "For ax²+bx+c=0, sum of roots = −b/a.")

SC = {
 "Science": [
  ("Plants & Photosynthesis", "Which gas do plants absorb for photosynthesis?", "Carbon dioxide", ["Oxygen", "Nitrogen", "Hydrogen"], "Plants take in CO₂ and release O₂ during photosynthesis.", 1, 10),
  ("Matter", "What is the chemical formula of water?", "H₂O", ["HO₂", "H₂O₂", "OH"], "Water has two hydrogen atoms and one oxygen atom.", 3, 10),
  ("Light", "The speed of light in vacuum is approximately:", "3 × 10⁸ m/s", ["3 × 10⁶ m/s", "3 × 10⁵ m/s", "300 m/s"], "Light travels at about 3×10⁸ m/s in vacuum.", 6, 10),
  ("Human body", "Which organ pumps blood around the body?", "Heart", ["Lungs", "Liver", "Kidney"], "The heart is a muscular pump of the circulatory system.", 1, 10),
  ("Acids & Bases", "pH of a neutral solution at 25°C is:", "7", ["0", "14", "1"], "pH 7 is neutral; below is acidic, above is basic.", 7, 10),
  ("Electricity", "Ohm's law states that V =", "IR", ["I/R", "R/I", "I+R"], "Voltage equals current times resistance.", 8, 10),
  ("Force & Motion", "SI unit of force is:", "Newton", ["Joule", "Watt", "Pascal"], "Force is measured in newtons (kg·m/s²).", 8, 10),
  ("Solar system", "Which planet is known as the Red Planet?", "Mars", ["Venus", "Jupiter", "Mercury"], "Iron oxide on its surface gives Mars a red colour.", 3, 10)],
 "English": [
  ("Grammar: Nouns", "Choose the plural of 'child'.", "children", ["childs", "childes", "childrens"], "'Child' has the irregular plural 'children'.", 1, 10),
  ("Grammar: Tenses", "Choose the correct form: She ___ to school every day.", "goes", ["go", "going", "gone"], "Third-person singular present simple takes -es: goes.", 2, 10),
  ("Vocabulary", "Choose the synonym of 'rapid'.", "fast", ["slow", "weak", "late"], "Rapid means quick or fast.", 4, 10),
  ("Vocabulary", "Choose the antonym of 'ancient'.", "modern", ["old", "historic", "aged"], "Ancient means very old; the opposite is modern.", 5, 10),
  ("Grammar: Prepositions", "The book is ___ the table.", "on", ["at", "by", "to"], "'On' shows a surface contact position.", 2, 10),
  ("Grammar: Voice", "Passive voice of 'She wrote a letter':", "A letter was written by her.", ["A letter is written by her.", "She was written a letter.", "A letter wrote her."], "Simple past active → was/were + past participle.", 7, 10),
  ("Grammar: Reported speech", "He said, 'I am tired.' → He said that he ___ tired.", "was", ["is", "were", "be"], "Present tense shifts back to past in reported speech.", 7, 10),
  ("Literary devices", "'The wind whispered' is an example of:", "Personification", ["Simile", "Metaphor", "Alliteration"], "Giving human action (whispering) to the wind.", 8, 10)],
}
for b in A.BOARDS:
    for s, items in SC.items():
        for t, text, ans, wrong, exp, lo, hi in items:
            for g in range(lo, hi + 1, 3):  # appears at a few grades so every board/subject has material
                mcq(b, g, s, t, text, ans, wrong, exp)

c.executemany("INSERT INTO questions(board,grade,subject,topic,qtype,text,options,answer,explanation,marks,source,year) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", Q)
# one practice test per board/grade/subject that has >=4 questions
n = 0
for b in A.BOARDS:
    for g in range(1, 11):
        for s in A.SUBJECTS:
            ids = [r[0] for r in c.execute("SELECT id FROM questions WHERE board=? AND grade=? AND subject=?", (b, g, s))]
            if len(ids) < 4: continue
            cur = c.execute("INSERT INTO tests(title,board,grade,subject,kind,duration) VALUES(?,?,?,?,?,?)",
                            (f"{s} Practice Test – Class {g}", b, g, s, "practice", 15))
            c.executemany("INSERT INTO test_questions VALUES(?,?)", [(cur.lastrowid, i) for i in ids[:10]]); n += 1
c.commit(); print(f"Seeded {len(Q)} questions, {n} tests")
