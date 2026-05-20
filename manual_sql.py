SQLS = {
    "task_163": """
        SELECT
            type,
            SUM(expense.cost) AS total_approved_value
        FROM event
        INNER JOIN budget
            ON event.event_id = budget.link_to_event
        INNER JOIN expense
            ON budget.budget_id = expense.link_to_budget
        WHERE event.event_name = 'October Meeting'
            AND expense.approved = TRUE
        GROUP BY type
    """,
    "task_169": """
        SELECT AVG(ym.Consumption) / 12 AS AvgMonthlyConsumption
        FROM yearmonth ym
        INNER JOIN customers c
            ON ym.CustomerID = c.CustomerID
        WHERE c.Segment = 'SME'
            AND ym.Date >= 201301
            AND ym.Date <= 201312
    """,
    "task_180": """
        SELECT DISTINCT y.CustomerID, y.Consumption
        FROM transactions_1k t
        INNER JOIN yearmonth y 
            ON t.CustomerID = y.CustomerID
        WHERE t.ProductID = 5
            AND t.Amount > 0
            AND (t.Price / t.Amount) > 29.00
            AND y.Date = 201208
    """,
    "task_173": """
        SELECT DISTINCT gs.Country
        FROM yearmonth ym
        INNER JOIN transactions_1k ts
            ON ym.CustomerID = ts.CustomerID
        INNER JOIN gasstations gs
            ON ts.GasStationID = gs.GasStationID
        WHERE ym.Date = 201306
        ORDER BY gs.Country
    """,
    "task_199": """
        SELECT "School Name", "Charter Funding Type"
        FROM frpm
        INNER JOIN satscores ss
            ON frpm.CDSCode = ss.cds
                AND rtype = 'S'
        WHERE ss.AvgScrMath > 400
            AND frpm."District Name" ILIKE '%Riverside%'
    """,
    "task_200": """
        SELECT COUNT(DISTINCT T1.atom_id)
        FROM atom AS T1
        INNER JOIN bond AS T2
            ON T1.molecule_id = T2.molecule_id
        WHERE T2.bond_type = '#'
            AND T1.element IN ('p', 'br')
    """,
    "task_218": """
        SELECT schools.Phone
        FROM satscores
        INNER JOIN schools 
            ON LPAD(satscores.cds, 14, '0') = schools.CDSCode
        WHERE satscores.rtype = 'S'
            AND schools.District = 'Fresno Unified'
        ORDER BY satscores.AvgScrRead ASC
        LIMIT 1
    """,
    "task_22": """
        SELECT date_received
        FROM income i
        INNER JOIN member m
            ON i."link_to_member" = m."member_id"
        WHERE m."first_name" = 'Connor'
            AND m."last_name" = 'Hilton'
    """,
    "task_249": """
        WITH pc AS (
            SELECT
                "OwnerUserId",
                COUNT(*) posts_count
            FROM posts p
            GROUP BY "OwnerUserId"
        )
        SELECT 
            AVG(Upvotes), 
            AVG(Age)
        FROM users u
        INNER JOIN pc 
            ON u.Id = pc."OwnerUserId"
        WHERE pc.posts_count > 10
    """,
    "task_25": """
        SELECT e.event_name AS event_name
        FROM expense exp
        INNER JOIN budget b
            ON exp."link_to_budget" = b."budget_id"
        INNER JOIN event e
            ON b."link_to_event" = e."event_id"
        GROUP BY e.event_name, e.event_id
        ORDER BY SUM(cost) ASC
        LIMIT 1
    """,
    "task_257": """
        SELECT
            p.ViewCount,
            u.DisplayName
        FROM posts p
        INNER JOIN users u
            ON u.Id = p.LastEditorUserId
        WHERE p.Title ILIKE 'Computer game datasets'
    """,
    "task_269": """
        SELECT superhero_name
        FROM superhero s
        INNER JOIN hero_power hp
            ON s."id" = hp."hero_id"
        INNER JOIN superpower sp
            ON hp."power_id" = sp."id"
        WHERE sp."power_name" ILIKE 'death touch'
    """,
    "task_80": """
        SELECT d.number
        FROM qualifying q
        INNER JOIN drivers d
            ON d.driverId = q.driverId
        WHERE q.raceId = 903
        AND q.q3 >= '1:54'
        AND q.q3 < '1:55'
        ORDER BY d.number
    """, 
    "task_86": """
        SELECT r."name"
        FROM driverStandings ds
        INNER JOIN races r
            ON ds."raceId" = r."raceId"
        INNER JOIN drivers d
            ON ds."driverId" = d."driverId"
        WHERE d."forename" ILIKE 'Alex'
            AND d."surname" ILIKE 'Yoong'
            AND ds."position" < 20
    """
}
