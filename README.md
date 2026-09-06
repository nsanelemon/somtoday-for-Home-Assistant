# SOMtoday voor Home Assistant

Deze versie maakt voor iedere leerling in het SOMtoday-account een afzonderlijk
Home Assistant-apparaat aan. Per leerling worden sensoren voor lessen, huiswerk
en cijfers en twee agenda's aangemaakt: **Rooster** en **Huiswerk**.

## Bijwerken zonder opnieuw instellen

1. Vervang `/config/custom_components/somtoday` door de gelijknamige map uit
   deze repo.
2. Herstart Home Assistant.
3. Herlaad zo nodig de SOMtoday-integratie via **Instellingen → Apparaten &
   diensten → SOMtoday → drie puntjes → Herladen**.

Verwijder de bestaande integratie niet. De configuratie en de bestaande
entiteits-ID's van de eerste leerling blijven behouden. De tweede leerling en
de kalenderentiteiten worden na het laden automatisch toegevoegd.

## Entiteiten per leerling

- Leerling
- Lessen vandaag, met het attribuut `lessen`
- Volgende les, met vak, tijden, lokaal, lesuur en docent als attributen
- Huiswerk openstaand, met maximaal 25 details in `items`
- Huiswerk morgen, met de bijbehorende details in `items`
- Huiswerk komende 7 dagen, met de bijbehorende details in `items`
- Laatste cijfer
- Kalender Rooster
- Kalender Huiswerk

SOMtoday kent drie huiswerksoorten: gekoppeld aan een afspraak, dag of week.
De integratie probeert ze alle drie op te halen. Als één endpoint op een school
niet beschikbaar is, blijven de andere gegevens bruikbaar.

De sensoren **Huiswerk morgen** en **Huiswerk komende 7 dagen** tellen in hun
status alleen het openstaande huiswerk. Hun attribuut `items` bevat zowel open
als afgerond huiswerk. Voor ESPHome-displays zijn daarnaast de vlakke
attributen `item_1_regel` t/m `item_5_regel` en `item_1_detail` t/m
`item_5_detail` beschikbaar. Een regel begint met `OPEN` of `KLAAR`.

Versie 0.3.2 controleert de leerling-ID bovendien opnieuw nadat SOMtoday het
huiswerk heeft teruggestuurd. Dit voorkomt dat een SOMtoday-server die het
queryfilter niet goed toepast hetzelfde huiswerk bij beide leerlingen toont.
Bij de entiteit **Leerling** staan hiervoor de diagnostische attributen
`huiswerk_ontvangen` en `huiswerk_na_leerlingfilter`.

## Voorbeeld dashboardkaart: agenda

Vervang de entiteits-ID's door de ID's die Home Assistant bij jou toont.

```yaml
type: calendar
entities:
  - calendar.naam_rooster
  - calendar.naam_huiswerk
initial_view: listWeek
```

## Voorbeeld dashboardkaart: huiswerk morgen

```yaml
type: markdown
title: Huiswerk morgen
content: >-
  {% set items = state_attr('sensor.naam_huiswerk_morgen', 'items') or [] %}
  {% for item in items %}
  - **{{ item.vak or 'Huiswerk' }}** — {{ item.onderwerp or item.omschrijving or 'Geen omschrijving' }}
  {% else %}
  Geen openstaand huiswerk voor morgen.
  {% endfor %}
```

Voor de komende week gebruik je dezelfde kaart met de sensor
`sensor.naam_huiswerk_komende_7_dagen`.

```yaml
type: markdown
title: 📚 Huiswerk naam
content: |
  {% set entity = 'sensor.naam_huiswerk_komende_7_dagen' %}
  {% set totaal = state_attr(entity, 'totaal') | int(0) %}
  {% set openstaand = state_attr(entity, 'openstaand') | int(0) %}
  {% set afgerond = state_attr(entity, 'afgerond') | int(0) %}
  {% set items = state_attr(entity, 'items') | default([], true) %}

  **Komende 7 dagen**

  📝 **{{ openstaand }} openstaand** · ✅ **{{ afgerond }} afgerond** · 📚 **{{ totaal }} totaal**

  ---

  {% for item in items %}
  ### {{ item.vak }}
  **{{ item.onderwerp }}**

  📅 {{ item.datum }}  
  🏷️ {{ item.type }}

  {% if item.omschrijving %}
  {{ item.omschrijving }}
  {% endif %}

  {% if not loop.last %}
  ---
  {% endif %}
  {% endfor %}
```

## Als de tweede leerling nog ontbreekt

Open de entiteit **Leerling** en controleer het attribuut
`leerlingen_in_account`. Staat daar `1`, dan levert SOMtoday voor deze login
maar één leerling terug. Staat daar `2`, maar verschijnt er slechts één
apparaat, herlaad dan de integratie en controleer het Home Assistant-logboek.


Credits voor het werk van https://github.com/elisaado/somtoday-api-docs om het mogelijk te maken