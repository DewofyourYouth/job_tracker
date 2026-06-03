import csv


def main():
    with open("data/applications.csv", newline='') as apps:
        reader = csv.DictReader(apps)
        for row in reader:
            print(row)


if __name__ == "__main__":
    main()