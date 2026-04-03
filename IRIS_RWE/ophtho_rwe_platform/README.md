# Ophthalmology Real-World Evidence (RWE) Platform

A comprehensive Streamlit-based application for managing, analyzing, and exporting real-world evidence data in ophthalmology. This platform provides secure patient data management, advanced analytics, and compliance-focused data export capabilities.

## Overview

The Ophthalmology RWE Platform is designed to support clinical research and real-world evidence collection in ophthalmology. It provides a secure, user-friendly interface for healthcare professionals and researchers to:

- Manage patient demographics and clinical information
- Record detailed visit and treatment data
- Perform comprehensive statistical analysis
- Export data in compliance with healthcare regulations
- Maintain data anonymity and security

## Key Features

### 1. **User Authentication & Security**
- Secure login system with session management
- Role-based access control
- Protected database operations

### 2. **Patient Entry Module**
- Comprehensive patient registration system
- Support for demographics, contact information, and medical history
- Data validation and error handling
- Easy patient lookup and record management

### 3. **Visit Entry Module**
- Detailed visit recording with clinical observations
- Support for multiple visit types and findings
- Treatment documentation
- Integration with patient records
- Date and status tracking

### 4. **Analytics Dashboard**
- Real-time data visualization
- Statistical analysis of patient populations
- Trend analysis and insights
- Interactive charts and metrics
- Customizable date range filtering

### 5. **Data Export & Compliance**
- Secure data export to multiple formats (CSV, Excel)
- Data anonymization for privacy protection
- Compliance-focused reporting
- Audit trail for data exports
- HIPAA-ready data handling

## Project Structure

```
ophtho_rwe_platform/
├── app.py                  ← Main Streamlit application
├── pages/                  ← Multi-page application views
│   ├── 00_Login.py         ← User authentication
│   ├── 01_Patient_Entry.py ← Patient data management
│   ├── 02_Visit_Entry.py   ← Visit and clinical data
│   ├── 03_Analytics.py     ← Statistical analysis & visualizations
│   └── 04_Data_Export.py   ← Data export & anonymization
├── auth/                   ← Authentication utilities
├── database/               ← Database models and helpers
├── components/             ← Reusable UI components
├── config/                 ← Configuration settings
├── database.py             ← Database connection management
├── anonymization.py        ← Data anonymization functions
├── ophtho_rwe.db           ← SQLite database (production)
├── requirements.txt        ← Python dependencies
├── .env                    ← Environment variables (not committed)
├── .gitignore              ← Git ignore rules
├── PLAN.md                 ← Project roadmap and future scope
└── README.md               ← This file
```

## Data Dictionary

All data is stored in a SQLite database (`ophtho_rwe.db`). No personally identifiable information (PII) is persisted; patients are identified by a one-way hash. The six core tables are described below.

---

### `patients` — Anonymised Demographics

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | Internal row ID |
| `patient_hash` | VARCHAR(64) | 64-char hex string | SHA-256 of name + DOB; unique per patient |
| `age_group` | VARCHAR(10) | e.g. `"50-59"`, `"60-69"` | Decade band; no exact DOB stored |
| `sex` | VARCHAR(10) | `Male` / `Female` / `Other` | |
| `ethnicity` | VARCHAR(40) | Free text (optional) | |
| `smoking_status` | VARCHAR(20) | `Never` / `Ex` / `Current` | |
| `diabetes` | BOOLEAN | `True` / `False` | Comorbidity flag |
| `hypertension` | BOOLEAN | `True` / `False` | Comorbidity flag |
| `created_at` | DATETIME | ISO-8601 UTC | Record creation timestamp |

---

### `diagnoses` — ICD-10 Conditions per Patient-Eye

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | |
| `patient_id` | INTEGER | FK → `patients.id` | |
| `eye` | VARCHAR(3) | `OD` / `OS` / `OU` | Right / Left / Both |
| `condition_code` | VARCHAR(10) | ICD-10 block code | e.g. `H35.31` (nAMD) |
| `icd10_code` | VARCHAR(10) | ICD-10 sub-code | Specific sub-classification |
| `condition_name` | VARCHAR(80) | See catalogue below | Short clinical label |
| `date_diagnosed` | DATETIME | ISO-8601 UTC (optional) | |
| `notes` | TEXT | Free text (optional) | |

**Condition catalogue:**

| Display label | `condition_code` | `condition_name` |
|---|---|---|
| Neovascular AMD (nAMD) | H35.31 | nAMD |
| Diabetic Macular Oedema (DME) | H36.0 | DME |
| Branch Retinal Vein Occlusion (BRVO) | H34.83 | BRVO |
| Central Retinal Vein Occlusion (CRVO) | H34.81 | CRVO |
| Retinal Vein Occlusion — unspecified | H34.9 | RVO |
| Glaucoma (neovascular / other) | H40.9 | Glaucoma |
| Age-related Cataract | H25.9 | Cataract |
| Polypoidal Choroidal Vasculopathy (PCV) | H35.30 | PCV |
| Myopic CNV | H44.20 | Myopic CNV |
| Other | H35.99 | Other |

---

### `visits` — Clinic Encounter Records

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | |
| `patient_id` | INTEGER | FK → `patients.id` | |
| `visit_date` | DATETIME | ISO-8601 UTC | Date of clinic encounter |
| `visit_number` | INTEGER | ≥ 1 | Sequential count per patient |
| `eye` | VARCHAR(3) | `OD` / `OS` / `OU` | |
| `visit_type` | VARCHAR(20) | `Loading` / `Maintenance` / `PRN` / `T&E` | Treatment phase |
| `clinician_code` | VARCHAR(20) | Anonymised ref (optional) | No clinician name stored |
| `site_code` | VARCHAR(20) | Anonymised ref (optional) | No site name stored |
| `notes` | TEXT | Free text (optional) | |

---

### `treatments` — Anti-VEGF / Other Injections

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | |
| `visit_id` | INTEGER | FK → `visits.id` | |
| `drug_name` | VARCHAR(60) | e.g. `Aflibercept`, `Ranibizumab`, `Faricimab`, `Bevacizumab` | |
| `drug_dose_mg` | FLOAT | > 0 (optional) | e.g. `2.0` mg for Aflibercept |
| `injection_number` | INTEGER | ≥ 1 (optional) | Cumulative injection count for this eye |
| `injection_site` | VARCHAR(20) | `IVT` / `Sub-tenon` / other (optional) | Route |
| `concomitant_medications` | TEXT | CSV (optional) | Other concurrent medications |
| `reason_for_change` | TEXT | Free text (optional) | Populated on drug switch |

---

### `outcomes` — Visual Acuity & OCT Measures per Visit

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | |
| `visit_id` | INTEGER | FK → `visits.id` | |
| `bcva_logmar` | FLOAT | 0.0 – 3.0 (optional) | LogMAR VA; 0.0 = 6/6; higher = worse |
| `bcva_etdrs_letters` | INTEGER | **0 – 100** (optional) | ETDRS letter score; higher = better |
| `bcva_snellen` | VARCHAR(10) | e.g. `"6/12"` (optional) | Snellen fraction |
| `crt_um` | INTEGER | **100 – 700 µm** (optional) | Central Retinal Thickness on OCT |
| `irf_present` | BOOLEAN | `True` / `False` (optional) | Intraretinal fluid present |
| `srf_present` | BOOLEAN | `True` / `False` (optional) | Subretinal fluid present |
| `ped_height_um` | INTEGER | 0 – 1000 µm (optional) | Pigment epithelial detachment height |
| `iop_mmhg` | FLOAT | 5 – 50 mmHg (optional) | Intraocular pressure |
| `bcva_change_from_baseline` | FLOAT | (optional) | Letters gained vs. baseline; positive = improvement |
| `crt_change_from_baseline` | INTEGER | (optional) | µm change vs. baseline; negative = drying |

---

### `adverse_events` — AE Records Linked to Visit

| Field | Type | Valid values / range | Notes |
|---|---|---|---|
| `id` | INTEGER | Auto-increment PK | |
| `visit_id` | INTEGER | FK → `visits.id` | |
| `ae_classification` | VARCHAR(60) | Controlled vocabulary (see below) | |
| `ae_type` | VARCHAR(80) | Display label | |
| `ae_category` | VARCHAR(40) | `Ocular` / `Systemic` | Derived from classification |
| `severity_grade` | INTEGER | **1 – 5** | CTCAE v5.0 grade; DB-enforced |
| `serious` | BOOLEAN | `True` / `False` | SAE flag |
| `related_to_treatment` | BOOLEAN | `True` / `False` (optional) | Causality assessment |
| `onset_date` | DATETIME | ISO-8601 UTC (optional) | |
| `resolution_date` | DATETIME | ISO-8601 UTC (optional) | |
| `resolved` | BOOLEAN | `True` / `False` | |
| `description` | TEXT | Free text (optional) | |

**AE classification vocabulary:**

| `ae_classification` value | Category | Always SAE? |
|---|---|---|
| `Endophthalmitis` | Ocular | Yes |
| `IOP spike (>30 mmHg)` | Ocular | No |
| `Subconjunctival haemorrhage` | Ocular | No |
| `Retinal detachment` | Ocular | Yes |
| `RPE tear` | Ocular | No |
| `Uveitis / Anterior chamber inflammation` | Ocular | No |
| `Vitreous floaters` | Ocular | No |
| `Arterial thromboembolic event` | Systemic | Yes |
| `Hypertension (worsening)` | Systemic | No |
| `Other` | — | No |

**CTCAE severity grade key (NCI CTCAE v5.0):**

| Grade | Severity |
|---|---|
| 1 | Mild — asymptomatic or mild symptoms |
| 2 | Moderate — minimal intervention indicated |
| 3 | Severe — hospitalisation or IV therapy indicated |
| 4 | Life-threatening — urgent intervention required |
| 5 | Death related to AE |

---

### `audit_log` — Immutable Access Trail

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER | Auto-increment PK |
| `timestamp` | DATETIME | UTC; indexed |
| `username` | VARCHAR(64) | Authenticated user; indexed |
| `action` | VARCHAR(60) | e.g. `LOGIN_SUCCESS`, `PAGE_ACCESS`, `EXPORT_CSV` |
| `detail` | VARCHAR(500) | Page name, applied filters, or export filename |
| `record_count` | INTEGER | Rows exported (populated on export actions) |
| `ip_address` | VARCHAR(45) | Reserved for future use |

Rows are INSERT-only; no application code path updates or deletes audit records.

---

## Setup & Installation

### Prerequisites
- Python 3.8 or higher
- pip or conda package manager

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd ophtho_rwe_platform
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Initialize the database:**
   ```bash
   python database.py
   ```

6. **Run the application:**
   ```bash
   streamlit run app.py
   ```

The application will be available at `http://localhost:8501`

## Usage Guide

### Login Page (00_Login.py)
- First-time users can create an account
- Returning users can log in with credentials
- Session management ensures secure access

### Patient Entry (01_Patient_Entry.py)
1. Navigate to "Patient Entry" from the sidebar
2. Enter patient demographics and medical history
3. System validates data before saving
4. Search for existing patients by ID or name

### Visit Entry (02_Visit_Entry.py)
1. Select a patient from the list
2. Record visit details, clinical findings
3. Document treatments and interventions
4. Save visit records to database

### Analytics (03_Analytics.py)
1. View interactive dashboards with key metrics
2. Filter data by date range, patient demographics
3. Analyze trends and statistical insights
4. Export visualizations for reports

### Data Export (04_Data_Export.py)
1. Select data to export (patients, visits, or both)
2. Choose export format (CSV, Excel)
3. Apply anonymization if required
4. Download exported file

## Development

### Adding New Pages
1. Create a new file in `pages/` directory
2. Follow Streamlit conventions
3. Import from components and database modules
4. Add navigation link in `app.py`

### Creating Components
1. Add reusable components to `components/` directory
2. Use consistent naming and documentation
3. Keep components modular and testable

### Database Functions
- Use database helpers from `database.py`
- Create models in `database/` for complex schemas
- Always validate input data
- Implement proper error handling

### Anonymization
- Use `anonymization.py` for PII masking
- Ensure HIPAA compliance
- Test anonymization thoroughly before deployment

## Configuration

### Environment Variables (.env)
```
DATABASE_PATH=./ophtho_rwe.db
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=localhost
DEBUG_MODE=False
```

### Streamlit Config (.streamlit/config.toml)
- Theme and layout settings
- Server configuration
- Security settings

## Security Best Practices

- Never commit `.env` file with secrets
- Database files are in `.gitignore`
- Use parameterized queries to prevent SQL injection
- Validate all user inputs
- Implement proper access controls
- Encrypt sensitive data in transit
- Regular security audits recommended

## Future Scope

See [PLAN.md](PLAN.md) for detailed roadmap including:
- Multi-site data aggregation
- Advanced ML-based analytics
- Mobile application support
- Integration with EHR systems
- Enhanced compliance features
- Real-time collaboration tools

## Future Improvements

### Short-term (Next 2-3 months)
- [ ] Implement API endpoints for data access
- [ ] Add advanced filtering to analytics dashboard
- [ ] Enhanced error logging and monitoring
- [ ] User preference customization
- [ ] Bulk import functionality

### Medium-term (3-6 months)
- [ ] Machine learning-based patient risk stratification
- [ ] Real-time data synchronization across multiple sites
- [ ] Mobile app for patient data entry
- [ ] Advanced reporting engine
- [ ] Data quality dashboard

### Long-term (6-12 months)
- [ ] Integration with major EHR systems (Epic, Cerner)
- [ ] Blockchain-based audit trail
- [ ] Federated learning for privacy-preserving analysis
- [ ] Multi-language support
- [ ] AI-powered clinical decision support

## Testing

Run tests with:
```bash
pytest tests/
```

## Contributing

1. Create a feature branch
2. Make your changes
3. Add/update tests
4. Submit a pull request
5. Ensure all tests pass

## License

[Your License Here]

## Support & Contact

For issues, feature requests, or questions:
- Create an issue in the repository
- Contact the development team
- See PLAN.md for future development roadmap

## Changelog

### Version 1.0.0 (Current)
- Initial release with core functionality
- Patient and visit data management
- Basic analytics and reporting
- Data export with anonymization
- User authentication and access control

---

**Last Updated:** 2026-04-03  
**Maintainers:** IRIS RWE Team
