"""
WICHTIG:
Läuft aktuell nur bis Python 3.12, da TensorFlow stand jetzt kein Python 3.13 oder höher unterstützt.

Datenquelle kann per DATA_SOURCE umgestellt werden. (aktuell simulate)
"simulate" → Simulationsdaten
"database" → Wirkliche Sensordaten aus der Datenbank

Modell und Scaler werden in
data/model.keras
und
data/scaler.npz
gespeichert.

Aufruf über:
python Train_LSTM.py
"""

# Imports

import os                           # Betriebssystem-Funktionen, z.B. zum Erstellen von Verzeichnissen
import numpy as np                  # Numerische Operationen, z.B. für Arrays und Matrizen
import pandas as pd                 # Datenanalyse, z.B. für DataFrames
import matplotlib.pyplot as plt     # Plotting-Funktionen, Diagramme erstellen

from sklearn.preprocessing import StandardScaler # Wichtig für die Normalisierung der Daten, damit das LSTM-Modell mit einheitlichen Skalen trainiert wird. Ohne Normalisierung könnte das Modell Schwierigkeiten haben, zu lernen, da die Werte in unterschiedlichen Größenordnungen liegen könnten.
from tensorflow.keras.models import Sequential # Keras-Sequenzmodell, Layer werden nacheinander hinzugefügt
from tensorflow.keras.layers import Input, LSTM, Dense, Dropout # LSTM-Layer für zeitliche Abhängigkeiten, Dense für voll verbundene Schichten, Dropout zur Vermeidung von Overfitting
from tensorflow.keras.optimizers import Adam # Adam-Optimierer, der die Lernrate während des Trainings automatisch anpasst
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau # Callbacks für das Training, z.B. um frühzeitig zu stoppen oder die Lernrate zu reduzieren, wenn der Fehler stagniert.

# Konfiguration

DATA_SOURCE = "simulate"  # "simulate" oder "database"
SIM_TOTAL_MINUTES = 3000  # Anzahl der simulierten Minuten, 3000 Minuten entsprechen 50 Stunden
DB_SENSOR_ID = "sensor01"  # ID des Sensors, dessen Daten aus der Datenbank verwendet werden sollen, nur relevant wenn DATA_SOURCE auf "database" gesetzt ist
SEQ_LENGTH = 40          # Länge der Eingabesequenzen für das LSTM-Modell, d.h. wie viele vorherige Zeitpunkte das Modell sehen soll, um die nächste Temperatur vorherzusagen
LSTM_UNITS = 64           # Anzahl der Neuronen in den LSTM-Schichten, mehr Neuronen können komplexere Muster lernen, aber auch zu Überanpassung führen
DROPOUT_RATE = 0.2       # Dropout-Rate für die Dropout-Schicht, um Overfitting zu vermeiden, 0.2 bedeutet, dass 20% der Neuronen während des Trainings zufällig deaktiviert werden
EPOCHS = 100            # Anzahl der Trainingsdurchläufe über den gesamten Datensatz, mehr Epochen können zu besserer Leistung führen, aber auch zu Überanpassung (Overfitting), EarlyStopping unterbricht das Training automatisch, wenn keine Verbesserung mehr eintritt
BATCH_SIZE = 32         # Anzahl der Trainingsbeispiele, die in einem Schritt durch das Modell verarbeitet werden, bevor die Gewichte aktualisiert werden
LEARNING_RATE = 0.001   # Anfangslernrate für den Adam-Optimierer, bestimmt, wie schnell das Modell lernt, ReduceLROnPlateau wird die Lernrate automatisch reduzieren, wenn der Fehler über mehrere Epochen hinweg nicht sinkt
TRAIN_SPLIT = 0.8         # Anteil der Daten, der für das Training verwendet wird, der Rest wird für die Validierung verwendet, z.B. 0.8 bedeutet, dass 80% der Daten zum Trainieren und 20% zum Validieren verwendet werden
FORECAST_MINUTES = 30  # Anzahl der Minuten, die das Modell in die Zukunft vorhersagen soll, z.B. 30 Minuten bedeutet, dass das Modell die Temperatur 30 Minuten nach dem letzten Zeitpunkt der Eingabesequenz vorhersagen soll

OUTPUT_DIR = "data"     # Verzeichnis, in dem das trainierte Modell und der Scaler gespeichert werden, z.B. "data/model.keras" für das Modell und "data/scaler.npz" für den Scaler
MODEL_PATH = os.path.join(OUTPUT_DIR, "model.keras") # Pfad zum Speichern des trainierten Modells
SCALER_PATH = os.path.join(OUTPUT_DIR, "scaler.npz") # Pfad zum Speichern des Scalers, der für die Normalisierung der Daten verwendet wird, damit die gleichen Skalierungsparameter auch bei der Vorhersage angewendet werden können

# Funktionen

# Simulierte Temperaturdaten generieren wenn DATA_SOURCE auf "simulate" gesetzt ist
def load_simulated_data(total_minutes: int) -> pd.DataFrame:
    """
    Generiert simulierte Temperaturdaten über einen Zeitraum von total_minutes Minuten.
    
    Die Temperatur wird als Kombination aus folgenden drei Komponenten modelliert:
    1. Tagesverlauf - 24h-Sinuskurve: simuliert Erwärmung am Tag und Abkühlung in der Nacht
    2. Kurze Schwankungen - 3h-Sinuskurve: simuliert kurzfristige Schwankungen, z.B. durch Heiz- Kühlzyklen oder Fensteröffnen
    3. Rauschen - Zufällige Normalverteilung: simuliert unvorhersehbare Faktoren wie bei realen Sensoren

    Gibt einen pandas DataFrame mit einer Spalte "temperature" zurück, die die simulierten Temperaturwerte enthält.
    """

    # np.arange erzeugt [0, 1, 2, ..., total_minutes-1].
    # Jeder Index steht für eine Minute seit Simulationsstart.
    minutes = np.arange(total_minutes)

    # Tagesverlauf: Sinuskurve mit Periode 1440 Min. (= 24h), Amplitude 1.8 °C.
    # Mittelwert 20 °C. Phasenversatz -2π*0.25 verschiebt das Minimum auf ~6 Uhr morgens.
    daily_wave = 20.0 + 1.8 * np.sin(minutes / 1440 * 2 * np.pi - 2 * np.pi * 0.25)

    # Kurzzeitwelle: Periode 180 Min. (= 3h), Amplitude 0.5 °C.
    # Phasenversatz 0.8 damit sie nicht synchron mit dem Tagesverlauf startet.
    short_wave = 0.5 * np.sin(minutes / 180 * 2 * np.pi + 0.8)

    # Gaußsches Rauschen: Mittelwert 0, Standardabweichung 0.08 °C.
    # np.random.normal(mu, sigma, n) zieht n zufällige Werte aus der Normalverteilung.
    noise = np.random.normal(0, 0.08, total_minutes)

    # Gesamttemperatur = Summe der drei Komponenten.
    temperature = daily_wave + short_wave + noise

    # Ergebnis als DataFrame mit einer Spalte "temperature".
    # np.round rundet auf 2 Nachkommastellen — wie ein echter Sensor.
    df = pd.DataFrame({"temperature": np.round(temperature, 2)})

    print(f"[Simulation] {total_minutes} Datenpunkte erzeugt. "
            f"Temp: {temperature.mean():.1f} ± {temperature.std():.2f} °C")
    return df


# Echte Sensordaten aus der Datenbank laden wenn DATA_SOURCE auf "database" gesetzt ist
def load_database_data() -> pd.DataFrame:
    """
    Lädt echte Temperaturmesswerte aus der PostgreSQL-Datenbank.

    Liest aus sensor_data (aktuelle Daten) und sensor_data_archive (ältere Daten),
    fügt beide zusammen und gibt sie chronologisch sortiert zurück.

    Voraussetzungen (via docker-compose bereitgestellt):
        - Umgebungsvariable DB_HOST (z.B. "postgres")
        - Umgebungsvariable DB_NAME (z.B. "sensor")
        - Docker-Secret /run/secrets/db_write_password
    """
    # psycopg2 ist der PostgreSQL-Treiber für Python.
    # Nur hier importiert, da er nur im Datenbankbetrieb benötigt wird.
    import psycopg2

    # Passwort aus der Docker-Secret-Datei lesen.
    # .strip() entfernt Zeilenumbrüche am Ende der Datei.
    password = open("/run/secrets/db_write_password").read().strip()

    # Verbindung zur Datenbank aufbauen.
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user="iot_write_user",
        password=password,
    )

    # SQL-Query: liest alle Temperaturwerte (unit='C') des Sensors
    # aus aktiver Tabelle UND Archivtabelle zusammen (UNION ALL),
    # chronologisch aufsteigend sortiert.
    query = """
        SELECT recorded_at, value
        FROM (
            SELECT recorded_at, value FROM sensor_data
            UNION ALL
            SELECT recorded_at, value FROM sensor_data_archive
        ) combined
        WHERE sensor_id = %(sensor_id)s
            AND unit = 'C'
        ORDER BY recorded_at ASC
    """

    # pandas liest das Abfrageergebnis direkt in einen DataFrame.
    # parse_dates stellt sicher, dass recorded_at als Datumsobjekt erkannt wird.
    df = pd.read_sql(
        query, conn,
        params={"sensor_id": DB_SENSOR_ID},
        parse_dates=["recorded_at"],
    )
    conn.close()  # Verbindung sofort freigeben wenn nicht mehr benötigt.

    # Spalte "value" in "temperature" umbenennen für einheitliche Weiterverarbeitung.
    df = df.rename(columns={"value": "temperature"}).reset_index(drop=True)

    print(f"[Datenbank] {len(df)} Temperaturwerte geladen.")

    # Mindestanzahl sicherstellen: zu wenige Daten = unbrauchbares Modell.
    if len(df) < SEQ_LENGTH * 5:
        raise ValueError(
            f"Zu wenige Datenpunkte ({len(df)}). "
            f"Mindestens {SEQ_LENGTH * 5} benötigt. "
            f"Nutze DATA_SOURCE='simulate' bis mehr Messdaten vorliegen."
        )
    return df


# Sliding-Window bereitet die Zeitreihe für das LSTM-Training vor
def create_sequences(data: np.ndarray, seq_length: int):
    """
    Wandelt die normalisierte Zeitreihe in überlappende Trainingspaare um.

    Das LSTM lernt nicht die gesamte Zeitreihe auf einmal, sondern immer
    einen Ausschnitt (Fenster) der Länge seq_length.

    Beispiel mit seq_length=3 und Werten [10, 11, 12, 13, 14]:
        X[0] = [10, 11, 12]  →  y[0] = 13
        X[1] = [11, 12, 13]  →  y[1] = 14

    Das Fenster verschiebt sich jedes Mal um einen Schritt (Sliding Window).

    Rückgabe:
        X — Shape (Anzahl Sequenzen, seq_length, 1): die Eingabefenster
        y — Shape (Anzahl Sequenzen, 1):             die zugehörigen Zielwerte
    """
    X, y = [], []

    # Fenster Schritt für Schritt durch die gesamte Zeitreihe schieben.
    for i in range(len(data) - seq_length):
        # Eingabe: seq_length aufeinanderfolgende Zeitschritte ab Position i.
        X.append(data[i : i + seq_length])
        # Zielwert: der direkt folgende Zeitschritt — was das Netz vorhersagen soll.
        y.append(data[i + seq_length])

    # Listen in NumPy-Arrays umwandeln.
    # float32 spart Speicher gegenüber float64 und ist TensorFlow-kompatibel.
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# LSTM-Modell aufbauen, genau wie in der Vorlesungsvorlage beschrieben
def build_model(input_shape: tuple) -> Sequential:
    """
    Erstellt das LSTM-Netzwerk mit zwei LSTM-Schichten und einer Ausgabeschicht.

    Architektur (entspricht exakt der Vorlage aus dem Skript):
        Input(SEQ_LENGTH Zeitschritte, 1 Feature)
            → LSTM(64, return_sequences=True)   alle Hidden States weitergeben
            → Dropout(0.2)
            → LSTM(64)                          nur letzten Hidden State ausgeben
            → Dropout(0.2)
            → Dense(1)                          ein Ausgabeneuron: vorhergesagte Temperatur
    """

    # Sequential: Layer werden der Reihe nach durchlaufen,
    # jeder bekommt den Output des vorherigen als Input.
    model = Sequential([

        # Input-Layer: legt die erwartete Form der Eingabedaten fest.
        # input_shape = (SEQ_LENGTH, 1) = 40 Zeitschritte, 1 Feature (Temperatur).
        Input(shape=input_shape),

        # Erster LSTM-Layer mit 64 Neuronen.
        # return_sequences=True: gibt den Hidden State h_t für JEDEN Zeitschritt t aus.
        # Das braucht der zweite LSTM-Layer als vollständige Sequenz als Eingabe.
        LSTM(LSTM_UNITS, return_sequences=True),

        # Dropout nach erstem LSTM: 20% der Verbindungen werden pro Schritt zufällig
        # abgeschaltet. Nur während des Trainings aktiv, bei der Vorhersage inaktiv.
        Dropout(DROPOUT_RATE),

        # Zweiter LSTM-Layer mit 64 Neuronen.
        # return_sequences=False (Standard): gibt nur den letzten Hidden State h_T aus.
        # Dieser fasst die gesamte Sequenz zu einem Vektor zusammen.
        LSTM(LSTM_UNITS),

        # Dropout nach zweitem LSTM.
        Dropout(DROPOUT_RATE),

        # Ausgabeschicht: ein Neuron ohne Aktivierungsfunktion.
        # Kein Softmax/ReLU da wir einen kontinuierlichen Wert (Regression) ausgeben,
        # kein Klassifizierungsproblem lösen.
        Dense(1),
    ])

    # Modell kompilieren: Optimierer und Verlustfunktion festlegen.
    model.compile(
        # Adam-Optimierer: adaptiv, berechnet individuelle Lernraten pro Parameter.
        optimizer=Adam(learning_rate=LEARNING_RATE),
        # MSE (Mean Squared Error): mittlerer quadratischer Fehler.
        # Formel: (1/N) * Σ(vorhergesagt - tatsächlich)²
        # Gut für Regressionsprobleme, bestraft große Abweichungen stärker.
        loss="mse",
    )

    # Modellübersicht ausgeben: Layer, Output-Shapes, Parameteranzahl.
    model.summary()
    return model


# Rekursiver Forecast: Schrittweise Vorhersage für die nächsten 'minutes' Minuten
def forecast_future(model, last_sequence: np.ndarray,
                    scaler: StandardScaler, minutes: int = 30,
                    alpha: float = 0.2) -> np.ndarray:
    """
    Berechnet Schritt für Schritt Vorhersagen für die nächsten 'minutes' Minuten.

    Prinzip (autoregressive / rekursive Vorhersage):
        1. Übergib die letzten 40 bekannten Werte → Modell sagt Minute 41 voraus
        2. Schiebe das Fenster: entferne Minute 1, füge vorhergesagte Minute 41 hinzu
        3. Wiederhole → Modell sagt Minute 42 voraus
        4. Usw. bis 'minutes' Schritte berechnet sind

    Alpha glättet den Übergang: new = (1-alpha)*vorhersage + alpha*letzter_bekannter_wert
    Damit werden abrupte Sprünge zwischen bekannten und vorhergesagten Werten vermieden.

    Eingabe: last_sequence bereits normalisiert (Ausgabe des Scalers).
    Ausgabe: Array der Shape (minutes,) in Original-Einheit (°C).
    """

    # Eingabesequenz als float32-Array sicherstellen.
    seq = np.array(last_sequence, dtype=np.float32)

    # Das Modell erwartet einen 3D-Tensor: (Batch-Größe, Zeitschritte, Features).
    # np.expand_dims fügt die Batch-Dimension vorne hinzu: (40, 1) → (1, 40, 1)
    if seq.ndim == 2:
        seq = np.expand_dims(seq, axis=0)

    future_scaled = []  # Sammelliste für die vorhergesagten (normierten) Werte

    for _ in range(minutes):
        # Modell gibt Vorhersage für den nächsten Zeitschritt aus.
        # verbose=0 unterdrückt die Ausgabe im Terminal.
        # pred hat Shape (1, 1): [[vorhergesagter_normierter_wert]]
        pred = model.predict(seq, verbose=0)

        # Exponentielles Glätten zwischen Vorhersage und letztem Sequenzwert.
        # seq[0, -1, 0] = letzter Zeitschritt der aktuellen Sequenz (normiert).
        smoothed = (1 - alpha) * float(pred[0, 0]) + alpha * float(seq[0, -1, 0])
        future_scaled.append(smoothed)

        # Vorhergesagten Wert als neuen Zeitschritt ans Ende der Sequenz anhängen.
        # reshape(1, 1, 1): skalarer Wert → 3D-Tensor passend zur Sequenzstruktur.
        next_val = np.array([[[smoothed]]], dtype=np.float32)

        # Fenster um einen Schritt verschieben:
        # seq[:, 1:, :] entfernt den ältesten Eintrag (links),
        # np.concatenate hängt den neuen Wert rechts an.
        # Die Sequenz bleibt immer genau SEQ_LENGTH lang.
        seq = np.concatenate([seq[:, 1:, :], next_val], axis=1)

    # Normierung rückgängig machen: transformiert die normierten Werte
    # zurück in die originale Einheit (°C).
    # scaler.inverse_transform erwartet 2D: (minutes, 1)
    future_scaled = np.array(future_scaled).reshape(-1, 1)
    return scaler.inverse_transform(future_scaled).flatten()  # → 1D-Array in °C


# Hauptfunktion: Alle Schritte von Datenladen bis Forecast-Test

def main():

    # Ausgabeordner anlegen falls noch nicht vorhanden.
    # exist_ok=True: kein Fehler wenn der Ordner bereits existiert.
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # SCHRITT 1: Daten laden

    print(f"\n=== Daten laden (Quelle: {DATA_SOURCE}) ===")

    if DATA_SOURCE == "simulate":
        df = load_simulated_data(SIM_TOTAL_MINUTES)
    elif DATA_SOURCE == "database":
        df = load_database_data()
    else:
        raise ValueError(f"Ungültige DATA_SOURCE: '{DATA_SOURCE}'. "
                            f"Erlaubt: 'simulate' oder 'database'.")

    # Temperaturspalte als 2D-NumPy-Array extrahieren: Shape (N, 1).
    # Das .values gibt das rohe NumPy-Array zurück, reshape(-1, 1) erzwingt 2D
    # weil StandardScaler und das LSTM eine 2D-Eingabe erwarten.
    features = df["temperature"].values.reshape(-1, 1)

    # SCHRITT 2: Normalisierung (StandardScaler)

    print("\n=== Normalisierung ===")

    # StandardScaler berechnet Mittelwert (µ) und Standardabweichung (σ)
    # und normalisiert: scaled = (wert - µ) / σ
    # Danach haben die Daten Mittelwert ≈ 0 und Standardabweichung ≈ 1.
    scaler = StandardScaler()

    # fit_transform: µ und σ berechnen (fit) und normalisieren (transform) in einem Schritt.
    scaled_data = scaler.fit_transform(features)

    print(f"Mittelwert (µ):        {scaler.mean_[0]:.2f} °C")
    print(f"Standardabweichung (σ): {scaler.scale_[0]:.2f}")

    # SCHRITT 3: Sliding-Window-Sequenzen erzeugen

    print(f"\n=== Sequenzen erzeugen (SEQ_LENGTH={SEQ_LENGTH}) ===")

    # Die normalisierte Zeitreihe in überlappende Eingabe-Ziel-Paare aufteilen.
    X, y = create_sequences(scaled_data, SEQ_LENGTH)

    # Trennindex: erste 80% → Training, letzte 20% → Validierung.
    split_idx = int(len(X) * TRAIN_SPLIT)

    # Aufteilen per Python-Slicing: [:split_idx] = alles bis zum Index, [split_idx:] = Rest.
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    print(f"Trainingssequenzen: {X_train.shape}  →  Zielwerte: {y_train.shape}")
    print(f"Testsequenzen:      {X_test.shape}   →  Zielwerte: {y_test.shape}")

    # SCHRITT 4: Modell aufbauen

    print("\n=== Modell erstellen ===")

    # input_shape = (Fenstergröße, Anzahl Features) = (40, 1).
    input_shape = (SEQ_LENGTH, 1)
    model = build_model(input_shape)

    # SCHRITT 5: Training

    print("\n=== Training ===")

    # Callbacks — werden nach jeder Epoche automatisch aufgerufen:
    callbacks = [
        EarlyStopping(
            monitor="val_loss",        # beobachtet den Validierungs-Loss
            patience=10,               # stoppt wenn 10 Epochen lang keine Verbesserung
            restore_best_weights=True, # lädt am Ende die Gewichte der besten Epoche
            verbose=1,
        ),
        ReduceLROnPlateau(
            monitor="val_loss",  # beobachtet den Validierungs-Loss
            factor=0.5,          # neue Lernrate = alte Lernrate × 0.5 (halbieren)
            patience=5,          # reagiert nach 5 Epochen ohne Verbesserung
            min_lr=1e-5,         # Untergrenze: Lernrate wird nie kleiner als 0.00001
            verbose=1,
        ),
    ]

    # Training starten:
    # Das Modell verarbeitet X_train (Eingaben) und vergleicht seine Ausgabe
    # mit y_train (Zielwerte). Der Unterschied (Loss) wird per Backpropagation
    # durch das Netz zurückgeleitet und die Gewichte werden angepasst.
    # Nach jeder Epoche wird der Fehler zusätzlich auf den Testdaten berechnet
    # (validation_data) — diese werden aber NICHT zum Trainieren verwendet.
    history = model.fit(
        X_train, y_train,
        validation_data=(X_test, y_test),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        verbose=1,
        callbacks=callbacks,
    )

    print(f"\nTraining abgeschlossen: "
            f"train_loss={history.history['loss'][-1]:.5f}, "
            f"val_loss={history.history['val_loss'][-1]:.5f}")

    # SCHRITT 6: Modell und Scaler speichern

    print(f"\n=== Speichern ===")

    # Trainiertes Modell im Keras-Format speichern.
    # controller.py lädt diese Datei beim Start um Forecasts durchzuführen.
    model.save(MODEL_PATH)

    # Scaler-Parameter (µ und σ) als NumPy-Archiv speichern.
    # Wird benötigt um Vorhersagen vom normierten Raum zurück in °C umzurechnen.
    np.savez(
        SCALER_PATH,
        mean=scaler.mean_,    # [µ_temperatur]
        scale=scaler.scale_,  # [σ_temperatur]
    )

    print(f"Modell gespeichert:  {MODEL_PATH}")
    print(f"Scaler gespeichert:  {SCALER_PATH}")

    # SCHRITT 7: Test-Forecast berechnen und ausgeben

    print(f"\n=== Forecast-Test ({FORECAST_MINUTES} Minuten) ===")

    # Die letzten SEQ_LENGTH normierten Werte als Startsequenz für den Forecast nehmen.
    last_seq = scaled_data[-SEQ_LENGTH:]  # Shape: (40, 1)

    # Forecast berechnen: 30 Minuten rekursiv in die Zukunft.
    forecast = forecast_future(model, last_seq, scaler, minutes=FORECAST_MINUTES)

    # Ergebnisse im Terminal ausgeben.
    print(f"Vorhersage der nächsten {FORECAST_MINUTES} Minuten:")
    for i, temp in enumerate(forecast, 1):
        print(f"  +{i:2d} min → {temp:.2f} °C")

    # SCHRITT 8: Diagramme erstellen und speichern

    # Zwei Diagramme nebeneinander in einem Fenster.
    fig, axes = plt.subplots(1, 2, figsize=(14, 4))

    # --- Linkes Diagramm: Trainingsverlauf ---
    # Zeigt wie sich Train-Loss und Val-Loss pro Epoche entwickelt haben.
    axes[0].plot(history.history["loss"],     label="Train Loss")
    axes[0].plot(history.history["val_loss"], label="Val Loss")
    axes[0].set_title("Trainingsverlauf (MSE Loss)")
    axes[0].set_xlabel("Epoche")
    axes[0].set_ylabel("MSE")
    axes[0].legend()
    axes[0].grid(True)

    # --- Rechtes Diagramm: Bekannte Werte + Forecast ---
    show_history = 60  # Wie viele vergangene Minuten angezeigt werden

    # Letzte 60 Temperaturwerte in Originaleinheit (°C, nicht normiert).
    hist_temp = features[-show_history:].flatten()

    # X-Achse: -60 bis 0 für bekannte Werte, 0 bis 30 für den Forecast.
    x_hist = np.arange(-show_history, 0)
    x_fore = np.arange(0, FORECAST_MINUTES)

    # Bekannte Werte als durchgezogene rote Linie.
    axes[1].plot(x_hist, hist_temp, color="tab:red", label="Temperatur (bekannt)")

    # Forecast als gestrichelte rote Linie (leicht transparent).
    axes[1].plot(x_fore, forecast, "--", color="tab:red", alpha=0.7, label="Temperatur (Forecast)")

    # Vertikale Linie bei x=0 markiert den Übergang von bekannt zu vorhergesagt.
    axes[1].axvline(0, color="gray", linestyle=":", label="jetzt")

    # Zielbereich des Reglers (19–21 °C) als grüne Fläche einzeichnen.
    axes[1].axhspan(19, 21, color="green", alpha=0.1, label="Zielbereich (19–21 °C)")

    axes[1].set_title(f"Forecast der nächsten {FORECAST_MINUTES} Minuten")
    axes[1].set_xlabel("Minuten (0 = jetzt)")
    axes[1].set_ylabel("Temperatur (°C)")
    axes[1].legend()
    axes[1].grid(True)

    # Abstände zwischen den Diagrammen automatisch anpassen.
    plt.tight_layout()

    # Plot als PNG speichern.
    plot_path = os.path.join(OUTPUT_DIR, "training_result.png")
    plt.savefig(plot_path, dpi=120)
    print(f"\nPlot gespeichert: {plot_path}")

    # Plot-Fenster anzeigen (Skript wartet hier bis das Fenster geschlossen wird).
    plt.show()


# EINSTIEGSPUNKT
# Dieser Block wird nur ausgeführt wenn das Skript direkt gestartet wird,
# nicht wenn es von einem anderen Skript importiert wird.

if __name__ == "__main__":
    main()