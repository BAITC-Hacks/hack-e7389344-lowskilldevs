# hack-e7389344-lowskilldevs
## Hackathon team repository for LowSkillDevs

**Track/Topic:** Logistics

The project is being developed for the EKT.KZ case at HackAlem AI

## Problem

Warehouse replenishment is currently calculated manually by consolidating data in Excel. Because detailed analysis is time-consuming, purchase orders are generated infrequently rather than in near real time. This creates two major problems:

- excess inventory and unnecessary storage costs;
- stock shortages and lost sales.

One-time bulk purchases, including unusually large purchases by a single customer, can also inflate estimates of recurring demand.

## Solution

The service analyzes each SKU (storage keeping unit) and generates a recommended supplier order with an explanation of the result. A procurement manager reviews and may adjust the recommendations before approving an order.

The primary workflow is:

1. The manager uploads data or starts a calculation for a warehouse or product category.
2. The service validates the data and identifies anomalous one-time sales.
3. The forecasting component estimates recurring demand using seasonality, trends, and stockout information.
4. The service calculates the required quantity using current inventory and incoming stock.
5. Recommendations are grouped by supplier and made available for review and export.
6. An authorized employee reviews and approves the order.

## Key Features

- replenishment calculations for each SKU;
- current inventory and incoming-stock adjustments;
- supplier lead-time and product-category support;
- seasonality and sustained demand-growth forecasting;
- lost-demand estimation for stockout periods;
- detection of one-time bulk orders and unusually large purchases by one customer;
- recommendations grouped by supplier;
- an explanation of every recommended quantity and urgency level;
- exportable order recommendations.

## Calculation Methodology

The service performs the following steps for each SKU.

### 1. Data preparation

- validate data types, dates, duplicates, and missing values;
- combine sales, inventory, incoming-stock, and supplier data;
- aggregate sales at the selected time interval;
- identify periods during which a product was unavailable.

### 2. One-time spike detection

One-time bulk sales are detected independently for each SKU. When an anonymized customer identifier is available, the service also checks whether an unusually large volume is concentrated under one customer.

A robust approach is recommended:

1. Estimate the typical sales volume using the median and median absolute deviation, or the interquartile range.
2. Flag observations that significantly exceed the expected range.
3. Determine whether the spike is isolated and associated with a single customer.
4. Exclude a confirmed anomaly from recurring-demand calculations or replace it with a robust estimate.
5. Preserve the original value and the reason for adjustment for auditability.

Seasonal peaks must not be classified automatically as outliers. The algorithm should compare each observation with equivalent seasonal periods and nearby values in the time series.

### 3. Stockout demand recovery

Zero or unusually low sales during a stockout do not necessarily indicate a lack of demand. Demand for these periods is estimated using adjacent periods, the seasonal profile, and, when appropriate, comparable products. The recovered value is used for forecasting and clearly marked as an estimate.

### 4. Recurring-demand forecast

The forecast is trained on the adjusted sales history and considers:

- baseline sales;
- seasonality;
- sustained trends;
- expected growth;
- estimated demand during stockout periods.

The forecasting model should be selected through time-based validation. For an MVP, seasonal averages, exponential smoothing, and a time-series model can be compared, with the best-performing approach selected using an agreed error metric.

### 5. Order calculation

```

After starting the service, open `TODO_LOCAL_URL`.

## Configuration

Create a `.env` file from `.env.example` and provide the required values:

```env
DATA_DIR=./data
OUTPUT_DIR=./outputs
FORECAST_HORIZON_DAYS=30
REVIEW_PERIOD_DAYS=7
SERVICE_LEVEL=0.95
```

## Usage Example

```bash
# TODO: replace this example with your application's actual interface
python app.py \
  --sales data/sales.csv \
  --inventory data/inventory.csv \
  --in-transit data/in_transit.csv \
  --suppliers data/suppliers.csv \
  --output outputs/recommendations.csv
```

## Acceptance Criteria

The minimum acceptance tests are:

- changing current inventory or incoming stock changes the final recommendation;
- a seasonal product receives a seasonal forecast rather than a simple historical average;
- estimated demand for a stockout period is greater than the result based only on recorded sales;
- adding an artificial one-time bulk sale does not significantly increase recurring demand;
- every recommendation includes an explanation and is assigned to a supplier;
- the system cannot send an order to a supplier without employee approval.

Useful evaluation metrics include:

- forecast WAPE or MAE on time-based validation data;
- stockout rate;
- product availability;
- inventory turnover and excess-inventory volume;
- percentage of recommendations adjusted by a procurement manager.

## Suggested Project Structure

Adapt this example to the actual repository:

```text
.
├── app/                  # API or user interface
├── src/
│   ├── data/             # data loading and validation
│   ├── preprocessing/    # cleaning and stockout processing
│   ├── forecasting/      # demand forecasting
│   ├── outliers/         # one-time order detection
│   └── ordering/         # recommendation calculation
├── tests/                # automated tests
├── data/                 # examples or synthetic data only
├── outputs/              # generated recommendations
├── .env.example
├── requirements.txt      # or the dependency file for your stack
└── README.md
```

## Constraints and Data Safety

- The service must not send an order to a supplier without approval from an authorized employee.
- Non-anonymized customer data must not be used.
- Outlier and stockout adjustments must remain explainable and reproducible.
- The output format must be compatible with the company's current accounting system.
- Recommendations support procurement decisions but do not replace manager review.

## Roadmap

- [ ] Connect sales-history and inventory exports.
- [ ] Implement baseline replenishment calculations by SKU.
- [ ] Add seasonality, trend, and stockout handling.
- [ ] Implement one-time bulk-order detection.
- [ ] Add explanations and supplier grouping.
- [ ] Implement recommendation export.
- [ ] Prioritize products by stockout risk.
- [ ] Support minimum order quantities and supplier-specific constraints.
- [ ] Add automated delivery of approved orders only after employee confirmation.

## Team

- Challenge owner: ekt.kz
- Development team: LowSkillDevs
- Contact: TODO_CONTACT





