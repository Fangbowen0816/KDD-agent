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
    """
}
